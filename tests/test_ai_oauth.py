"""改图助手的账号登录（设备码 OAuth，`engine/ai_oauth.py`，ADR 0146）。

厂商：生产表里只有 MiniMax；通用 RFC 8628 流程（authorization_pending / slow_down / expired_token……）用例里的
**合成厂商** `rfcvendor` 覆盖（`FLAVOR_RFC8628`，端点指向假服务器），不涉及任何真实厂商。

**绝不对真实的厂商 授权服务器发请求**：本文件把厂商规格表里的端点全部指到本机
`127.0.0.1` 随机端口上的假授权服务器（`http.server` 线程），client_id 用环境变量注入一个
非 UUID 的占位串。判据的主语：

* 流程：发起 → 轮询（pending / slow_down）→ signed_in | expired | denied | cancelled，同一厂商同时只有一个；
* 令牌：只存用户配置（0600），`public()` / capabilities / 日志 / 诊断包里一个字节都没有；
* 刷新：剩余不足 10 分钟在 spawn 之前刷，且回到签发它的区域端点；刷不动不 spawn；
* client_id：源码里没有别家的（也没有任何 UUID 形状的常量），为 None 时入口不出现。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
import time
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from tavotto.engine import ai_agents, ai_bridge, ai_oauth, ai_providers, config

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "tavotto"
CID = "test-client-id-not-a-real-one"


# ---------------------------------------------------------------------------
# 本机假授权服务器
# ---------------------------------------------------------------------------
class FakeAuth:
    """按路径脚本化应答。`script[path]` 是应答队列（元素是 `(status, body)` 或 `callable(form) -> (status, body)`），
    取完后重复最后一个；没脚本的路径回 404。所有请求（路径 / 表单 / 头）记在 `requests`。"""

    def __init__(self):
        self.requests: list[dict] = []
        self.script: dict[str, list] = {}
        self.gate: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # 静默
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
                with fake._lock:
                    fake.requests.append(
                        {"path": self.path, "form": form, "headers": dict(self.headers.items())}
                    )
                    queue = fake.script.get(self.path)
                    item = (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else None
                gate = fake.gate.get(self.path)
                if gate is not None:
                    gate.wait(5)
                if item is None:
                    status, body = 404, {"error": "not_found"}
                else:
                    status, body = item(form) if callable(item) else item
                if status in (301, 302, 307):
                    self.send_response(status)
                    self.send_header("Location", body)
                    self.end_headers()
                    return
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def on(self, path: str, *responses):
        self.script[path] = list(responses)

    def hits(self, path: str) -> list[dict]:
        return [r for r in self.requests if r["path"] == path]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


RFC_DEVICE = "/api/oauth/device_authorization"
RFC_TOKEN = "/api/oauth/token"
RFC_DEVICE_OK = (
    200,
    {
        "user_code": "RFC-1234",
        "device_code": "DEVICE-CODE-SECRET",
        "verification_uri_complete": "https://example.invalid/verify?code=RFC-1234",
        "interval": 0.01,
        "expires_in": 600,
    },
)
RFC_PENDING = (400, {"error": "authorization_pending"})
RFC_TOKENS = (
    200,
    {
        "access_token": "AT-RFC-SECRET",
        "refresh_token": "RT-RFC-SECRET",
        "expires_in": 3600,
        "scope": "rfcvendor",
        "token_type": "Bearer",
    },
)

MM_DEVICE = {"cn": "/cn/oauth2/device/code", "global": "/global/oauth2/device/code"}
MM_TOKEN = {"cn": "/cn/oauth2/token", "global": "/global/oauth2/token"}


def mm_device_ok(**over):
    def respond(form):
        body = {
            "user_code": "MM-5678",
            "verification_uri": "https://example.invalid/mm/verify",
            "expired_in": int(time.time() * 1000) + 600_000,  # 授权端点给的是绝对毫秒
            "interval": 10,  # MiniMax 是毫秒
            "state": form["state"],
        }
        body.update(over)
        return 200, body

    return respond


MM_PENDING = (200, {"status": "pending"})
MM_TOKENS = (
    200,
    {
        "status": "success",
        "access_token": "AT-MM-SECRET",
        "refresh_token": "RT-MM-SECRET",
        "expired_in": 7200,
    },
)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    srv = FakeAuth()
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "cfg"))
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setitem(ai_oauth.CLIENT_IDS, "rfcvendor", CID)
    monkeypatch.setenv("TAVOTTO_OAUTH_CLIENT_ID_MINIMAX", CID)
    specs = deepcopy(ai_oauth._SPECS)
    specs["rfcvendor"] = {
        "flavor": ai_oauth.FLAVOR_RFC8628,
        "label": "RFC Vendor",
        "default_region": "global",
        "device_path": "/api/oauth/device_authorization",
        "token_path": "/api/oauth/token",
        "regions": {
            "global": {"oauth_base": srv.base, "base_url": "https://api.example.invalid/coding/"}
        },
    }
    specs["minimax"]["regions"]["cn"]["oauth_base"] = srv.base + "/cn"
    specs["minimax"]["regions"]["global"]["oauth_base"] = srv.base + "/global"
    monkeypatch.setattr(ai_oauth, "_SPECS", specs)
    monkeypatch.setattr(ai_oauth, "MIN_INTERVAL_S", 0.01)
    monkeypatch.setattr(ai_oauth, "SLOW_DOWN_STEP_S", 0.05)
    monkeypatch.setattr(ai_oauth, "REFRESH_RETRY_SLEEPS_S", (0.0, 0.0))
    monkeypatch.setattr(ai_agents, "_run_probe", lambda argv, timeout=10: None)
    ai_bridge.invalidate_capabilities()
    ai_oauth.reset_for_tests()
    yield srv
    ai_oauth.reset_for_tests()
    srv.close()
    ai_bridge.invalidate_capabilities()


def wait_for(flow_id, states, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        st = ai_oauth.status(flow_id)
        if st and st["state"] in states:
            return st
        time.sleep(0.01)
    raise AssertionError(f"流程没到 {states}: {ai_oauth.status(flow_id)}")


def signed_in_record(
    provider="rfcvendor", region=None, *, expires_in_ms=3_600_000, endpoint=None, **over
):
    """直接写一条已登录的记录（跳过设备流程），用于刷新类用例。"""
    spec = ai_oauth._SPECS[provider]
    region = region or spec["default_region"]
    token_endpoint = endpoint or spec["regions"][region]["oauth_base"] + spec["token_path"]
    oauth = {
        "access": "AT-OLD-SECRET",
        "refresh": "RT-OLD-SECRET",
        "expires_ms": int(time.time() * 1000) + expires_in_ms,
        "token_endpoint": token_endpoint,
        "provider": provider,
        "region": region,
        **over,
    }
    return ai_providers.store_oauth(ai_oauth._record_defaults(provider, region), oauth)


SECRETS = ("AT-RFC-SECRET", "RT-RFC-SECRET", "AT-MM-SECRET", "RT-MM-SECRET", "DEVICE-CODE-SECRET")


# ---------------------------------------------------------------------------
# 流程（通用 RFC 8628，合成厂商）
# ---------------------------------------------------------------------------
def test_rfc8628_full_flow_stores_a_record_and_never_exposes_tokens(fake, caplog):
    caplog.set_level(logging.DEBUG)
    fake.on(RFC_DEVICE, RFC_DEVICE_OK)
    fake.on(RFC_TOKEN, RFC_PENDING, RFC_PENDING, RFC_TOKENS)
    flow = ai_oauth.start("rfcvendor")
    assert flow["state"] == "waiting" and flow["user_code"] == "RFC-1234"
    assert flow["verification_uri_complete"].endswith("code=RFC-1234")
    assert "device_code" not in flow and "DEVICE-CODE-SECRET" not in json.dumps(flow)
    done = wait_for(flow["flow_id"], {"signed_in"})
    assert done["endpoint_id"] == "rfcvendor-oauth"

    # 请求形状：client_id 来自注入、表单字段、如实标识（不冒充 kimi_cli）
    dev = fake.hits(RFC_DEVICE)[0]
    assert dev["form"] == {"client_id": CID}
    tok = fake.hits(RFC_TOKEN)[0]
    assert tok["form"]["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
    assert tok["form"]["device_code"] == "DEVICE-CODE-SECRET" and tok["form"]["client_id"] == CID
    for req in fake.requests:
        h = {k.lower(): v for k, v in req["headers"].items()}
        assert h["user-agent"].startswith("Tavotto/")
        assert not any(k.startswith("x-msh") for k in h)

    # 记录：agent=claude、合成厂商端点、令牌在用户配置里、文件 0600
    rec = ai_providers.get("rfcvendor-oauth")
    assert rec["agent"] == "claude" and rec["base_url"] == "https://api.example.invalid/coding"
    assert rec["auth"] == "oauth" and rec["api_key"] == ""
    assert rec["oauth"]["access"] == "AT-RFC-SECRET" and rec["oauth"]["refresh"] == "RT-RFC-SECRET"
    assert rec["oauth"]["token_endpoint"] == fake.base + RFC_TOKEN
    assert rec["oauth"]["provider"] == "rfcvendor" and rec["oauth"]["region"] == "global"
    assert rec["oauth"]["expires_ms"] > time.time() * 1000
    import os

    if os.name != "nt":
        assert config.config_path().stat().st_mode & 0o777 == 0o600
        assert config.config_dir().stat().st_mode & 0o777 == 0o700

    # 令牌不回传：public / capabilities / 日志
    pub = ai_providers.public(rec)
    assert pub["auth"] == "oauth" and pub["signed_in"] is True and pub["has_key"] is True
    assert pub["oauth_expires_ms"] == rec["oauth"]["expires_ms"] and "oauth" not in pub
    blob = json.dumps(ai_bridge.capabilities(refresh=True)) + caplog.text
    for s in SECRETS:
        assert s not in blob
    assert "RFC-1234" not in caplog.text  # 验证码也不进日志


def test_pending_then_slow_down_widens_the_interval_then_succeeds(fake):
    fake.on(RFC_DEVICE, RFC_DEVICE_OK)
    fake.on(RFC_TOKEN, RFC_PENDING, (400, {"error": "slow_down"}), RFC_PENDING, RFC_TOKENS)
    flow = ai_oauth.start("rfcvendor")
    base_interval = flow["interval"]
    done = wait_for(flow["flow_id"], {"signed_in"})
    assert len(fake.hits(RFC_TOKEN)) == 4
    assert done["interval"] == pytest.approx(base_interval + ai_oauth.SLOW_DOWN_STEP_S)


@pytest.mark.parametrize(
    ("answer", "state"),
    [
        ((400, {"error": "expired_token"}), "expired"),
        ((400, {"error": "access_denied"}), "denied"),
        ((400, {"error": "invalid_request"}), "failed"),
    ],
)
def test_terminal_errors_leave_no_record(fake, answer, state):
    fake.on(RFC_DEVICE, RFC_DEVICE_OK)
    fake.on(RFC_TOKEN, RFC_PENDING, answer)
    flow = ai_oauth.start("rfcvendor")
    assert wait_for(flow["flow_id"], {state})["state"] == state
    assert ai_providers.get("rfcvendor-oauth") is None
    assert "device_code" not in json.dumps(ai_oauth.status(flow["flow_id"]))


def test_device_deadline_passes_without_server_error_is_expired(fake):
    body = dict(RFC_DEVICE_OK[1], expires_in=0.15)
    fake.on(RFC_DEVICE, (200, body))
    fake.on(RFC_TOKEN, RFC_PENDING)
    flow = ai_oauth.start("rfcvendor")
    assert wait_for(flow["flow_id"], {"expired"})["state"] == "expired"
    assert ai_providers.get("rfcvendor-oauth") is None


def test_transient_server_errors_are_retried_but_not_forever(fake):
    fake.on(RFC_DEVICE, RFC_DEVICE_OK)
    fake.on(RFC_TOKEN, (503, {}), (502, {}), RFC_TOKENS)
    flow = ai_oauth.start("rfcvendor")
    wait_for(flow["flow_id"], {"signed_in"})
    ai_oauth.reset_for_tests()
    fake.on(RFC_TOKEN, (503, {}))
    flow2 = ai_oauth.start("rfcvendor")
    assert wait_for(flow2["flow_id"], {"failed"})["state"] == "failed"
    assert len(fake.hits(RFC_TOKEN)) >= 3 + ai_oauth.MAX_POLL_TRANSIENT_FAILURES


def test_cancel_stops_polling_and_a_late_success_is_discarded(fake):
    fake.on(RFC_DEVICE, RFC_DEVICE_OK)
    fake.on(RFC_TOKEN, RFC_PENDING)
    flow = ai_oauth.start("rfcvendor")
    time.sleep(0.1)
    assert ai_oauth.cancel(flow["flow_id"])["state"] == "cancelled"
    seen = len(fake.hits(RFC_TOKEN))
    time.sleep(0.15)
    assert len(fake.hits(RFC_TOKEN)) <= seen + 1  # 至多一个在途请求
    assert ai_oauth.cancel("nope") is None and ai_oauth.status("nope") is None

    # 在途轮询请求期间被取消：服务端随后给出令牌，也不能落盘
    ai_oauth.reset_for_tests()
    fake.on(RFC_TOKEN, RFC_TOKENS)
    fake.gate[RFC_TOKEN] = threading.Event()
    before = len(fake.hits(RFC_TOKEN))
    flow2 = ai_oauth.start("rfcvendor")
    end = time.time() + 5
    while len(fake.hits(RFC_TOKEN)) == before and time.time() < end:
        time.sleep(0.01)
    ai_oauth.cancel(flow2["flow_id"])
    fake.gate[RFC_TOKEN].set()
    time.sleep(0.2)
    assert ai_oauth.status(flow2["flow_id"])["state"] == "cancelled"
    assert ai_providers.get("rfcvendor-oauth") is None


def test_cancel_racing_the_token_commit_never_leaves_cancelled_with_tokens(fake, monkeypatch):
    """Codex #1326：成功轮询已过了「是否取消」检查、`store_oauth` 落盘之前，用户关闭对话框（cancel）。
    判据的主语：流程状态与配置里有没有令牌必须一致——cancelled 就一个字节都没有，signed_in 就有。"""
    fake.on(RFC_DEVICE, RFC_DEVICE_OK)
    fake.on(RFC_TOKEN, RFC_TOKENS)
    entered, release = threading.Event(), threading.Event()
    real = ai_providers.store_oauth

    def paused(defaults, oauth):
        entered.set()  # 轮询线程停在「拿到令牌之后、落盘之前」
        release.wait(1.0)
        return real(defaults, oauth)

    monkeypatch.setattr(ai_providers, "store_oauth", paused)
    flow = ai_oauth.start("rfcvendor")
    assert entered.wait(5)
    canceller = threading.Thread(target=lambda: ai_oauth.cancel(flow["flow_id"]))
    canceller.start()
    time.sleep(0.2)  # 修复前 cancel 此刻已把流程改成 cancelled；修复后它在等落盘那把锁
    release.set()
    canceller.join(5)
    assert not canceller.is_alive()
    ai_oauth._FLOWS[flow["flow_id"]].thread.join(5)  # 轮询线程收尾后再看盘
    state = wait_for(flow["flow_id"], {"signed_in", "cancelled"})["state"]
    rec = ai_providers.get("rfcvendor-oauth")
    has_tokens = bool(rec and (rec.get("oauth") or {}).get("access"))
    assert has_tokens == (state == "signed_in"), (state, has_tokens)


def test_same_provider_has_only_one_flow(fake):
    fake.on(RFC_DEVICE, RFC_DEVICE_OK)
    fake.on(RFC_TOKEN, RFC_PENDING)
    results: list[dict] = []
    threads = [
        threading.Thread(target=lambda: results.append(ai_oauth.start("rfcvendor")))
        for _ in range(6)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({r["flow_id"] for r in results}) == 1
    assert len(fake.hits(RFC_DEVICE)) == 1
    assert sum(1 for t in threading.enumerate() if t.name == "ai-oauth-rfcvendor") == 1


def test_start_failures_do_not_leave_flows_or_follow_redirects(fake):
    fake.on(RFC_DEVICE, (302, "http://127.0.0.1:9/stolen"))
    with pytest.raises(ai_oauth.OAuthError) as exc:
        ai_oauth.start("rfcvendor")
    assert exc.value.code == "oauth_start_failed"
    fake.on(RFC_DEVICE, (500, {}))
    with pytest.raises(ai_oauth.OAuthError):
        ai_oauth.start("rfcvendor")
    fake.on(RFC_DEVICE, (200, {"user_code": "x"}))  # 缺 device_code / 网址
    with pytest.raises(ai_oauth.OAuthError):
        ai_oauth.start("rfcvendor")
    fake.on(RFC_DEVICE, (200, b'{"user_code": "' + b"x" * 70_000 + b'"}'))  # 超过响应体上限
    with pytest.raises(ai_oauth.OAuthError):
        ai_oauth.start("rfcvendor")
    assert not ai_oauth._FLOWS


# ---------------------------------------------------------------------------
# 流程（MiniMax / PKCE + user_code）
# ---------------------------------------------------------------------------
def test_minimax_flow_uses_pkce_state_and_region_endpoints(fake):
    fake.on(MM_DEVICE["global"], mm_device_ok())
    fake.on(MM_TOKEN["global"], MM_PENDING, MM_TOKENS)
    flow = ai_oauth.start("minimax", "global")
    assert flow["region"] == "global" and flow["user_code"] == "MM-5678"
    wait_for(flow["flow_id"], {"signed_in"})
    dev = fake.hits(MM_DEVICE["global"])[0]
    assert dev["form"]["scope"] == "group_id profile model.completion"
    assert dev["form"]["code_challenge_method"] == "S256" and dev["form"]["client_id"] == CID
    assert dev["headers"].get("x-request-id") or dev["headers"].get("X-Request-Id")
    tok = fake.hits(MM_TOKEN["global"])[-1]
    assert tok["form"]["grant_type"] == "urn:ietf:params:oauth:grant-type:user_code"
    assert tok["form"]["user_code"] == "MM-5678"
    # code_verifier 的 S256 就是发出去的 code_challenge
    digest = hashlib.sha256(tok["form"]["code_verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == dev["form"]["code_challenge"]
    assert not fake.hits(MM_DEVICE["cn"])  # 没有串到另一个区域
    rec = ai_providers.get("minimax-oauth")
    assert rec["base_url"] == "https://api.minimax.io/anthropic"
    assert (
        rec["oauth"]["region"] == "global"
        and rec["oauth"]["token_endpoint"] == fake.base + MM_TOKEN["global"]
    )
    assert rec["label"].startswith("MiniMax")


def test_minimax_cn_uses_cn_base_url_and_default_region_is_cn(fake):
    fake.on(MM_DEVICE["cn"], mm_device_ok())
    fake.on(MM_TOKEN["cn"], MM_TOKENS)
    flow = ai_oauth.start("minimax")  # 不给区域 = 国内
    wait_for(flow["flow_id"], {"signed_in"})
    assert ai_providers.get("minimax-oauth")["base_url"] == "https://api.minimaxi.com/anthropic"


def test_minimax_state_mismatch_is_refused(fake):
    fake.on(MM_DEVICE["cn"], mm_device_ok(state="someone-elses"))
    with pytest.raises(ai_oauth.OAuthError):
        ai_oauth.start("minimax", "cn")


def test_minimax_error_status_fails_the_flow(fake):
    fake.on(MM_DEVICE["cn"], mm_device_ok())
    fake.on(
        MM_TOKEN["cn"], MM_PENDING, (200, {"status": "error", "base_resp": {"status_msg": "boom"}})
    )
    flow = ai_oauth.start("minimax", "cn")
    assert wait_for(flow["flow_id"], {"failed"})["state"] == "failed"


def test_switching_region_cancels_the_old_flow(fake):
    fake.on(MM_DEVICE["cn"], mm_device_ok())
    fake.on(MM_DEVICE["global"], mm_device_ok())
    fake.on(MM_TOKEN["cn"], MM_PENDING)
    fake.on(MM_TOKEN["global"], MM_PENDING)
    a = ai_oauth.start("minimax", "cn")
    b = ai_oauth.start("minimax", "global")
    assert a["flow_id"] != b["flow_id"]
    assert ai_oauth.status(a["flow_id"])["state"] == "cancelled"
    assert ai_oauth.status(b["flow_id"])["state"] == "waiting"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (7200, lambda now: now + 7_200_000),  # 相对秒
        ("3600", lambda now: now + 3_600_000),  # 字符串数字
        (1_900_000_000, lambda now: 1_900_000_000_000),  # 绝对秒
        (1_900_000_000_000, lambda now: 1_900_000_000_000),  # 绝对毫秒
        (999_999_999, lambda now: now + 999_999_999_000),  # 相对秒的上界
        (0, None),
        (-5, None),
        ("abc", None),
        (None, None),
        (True, None),
        (float("nan"), None),
    ],
)
def test_expiry_accepts_relative_seconds_absolute_seconds_and_absolute_ms(value, expected):
    now = 1_700_000_000_000
    out = ai_oauth.resolve_expiry_ms(value, now)
    assert out == (expected(now) if expected else None)


# ---------------------------------------------------------------------------
# 刷新
# ---------------------------------------------------------------------------
def test_fresh_token_is_not_refreshed_and_no_request_is_made(fake):
    rec = signed_in_record(expires_in_ms=3_600_000)
    assert ai_oauth.ensure_fresh(rec)["oauth"]["access"] == "AT-OLD-SECRET"
    assert fake.requests == []


def test_near_expiry_refreshes_and_writes_back(fake):
    rec = signed_in_record(expires_in_ms=5 * 60 * 1000)  # 不足 10 分钟
    fake.on(
        RFC_TOKEN, (200, {"access_token": "AT-NEW", "refresh_token": "RT-NEW", "expires_in": 3600})
    )
    out = ai_oauth.ensure_fresh(rec)
    assert out["oauth"]["access"] == "AT-NEW" and out["oauth"]["refresh"] == "RT-NEW"
    form = fake.hits(RFC_TOKEN)[0]["form"]
    assert form == {
        "grant_type": "refresh_token",
        "client_id": CID,
        "refresh_token": "RT-OLD-SECRET",
    }
    stored = ai_providers.get("rfcvendor-oauth")["oauth"]
    assert stored["access"] == "AT-NEW" and stored["expires_ms"] > time.time() * 1000 + 3_000_000
    assert stored["token_endpoint"] == fake.base + RFC_TOKEN and stored["provider"] == "rfcvendor"


def test_refresh_without_rotation_keeps_the_old_refresh_token(fake):
    rec = signed_in_record(expires_in_ms=1000)
    fake.on(RFC_TOKEN, (200, {"access_token": "AT-NEW", "expires_in": 3600}))
    out = ai_oauth.ensure_fresh(rec)
    assert out["oauth"]["refresh"] == "RT-OLD-SECRET"


@pytest.mark.parametrize("region", ["cn", "global"])
def test_refresh_goes_back_to_the_region_that_issued_the_token(fake, region):
    other = "global" if region == "cn" else "cn"
    rec = signed_in_record("minimax", region, expires_in_ms=1000)
    fake.on(
        MM_TOKEN[region], (200, {"status": "success", "access_token": "AT-NEW", "expired_in": 3600})
    )
    fake.on(
        MM_TOKEN[other], (200, {"status": "success", "access_token": "WRONG", "expired_in": 3600})
    )
    out = ai_oauth.ensure_fresh(rec)
    assert out["oauth"]["access"] == "AT-NEW"
    assert fake.hits(MM_TOKEN[region]) and not fake.hits(MM_TOKEN[other])


def test_refresh_never_sends_the_token_to_an_endpoint_not_in_the_known_table(fake):
    rec = signed_in_record(expires_in_ms=1000, endpoint="http://127.0.0.1:9/steal")
    with pytest.raises(ai_oauth.NeedsLogin):
        ai_oauth.ensure_fresh(rec)
    assert fake.requests == []


def test_rejected_refresh_needs_login_even_if_token_not_yet_expired(fake):
    rec = signed_in_record(expires_in_ms=5 * 60 * 1000)
    fake.on(RFC_TOKEN, (400, {"error": "invalid_grant"}))
    with pytest.raises(ai_oauth.NeedsLogin):
        ai_oauth.ensure_fresh(rec)
    assert len(fake.hits(RFC_TOKEN)) == 1  # 4xx 不重试
    assert ai_providers.get("rfcvendor-oauth")["oauth"]["access"] == "AT-OLD-SECRET"  # 旧令牌原样


def test_minimax_in_band_errors_on_200_are_rejections(fake):
    rec = signed_in_record("minimax", "cn", expires_in_ms=1000)
    fake.on(MM_TOKEN["cn"], (200, {"status": "error", "base_resp": {"status_msg": "bad"}}))
    with pytest.raises(ai_oauth.NeedsLogin):
        ai_oauth.ensure_fresh(rec)
    fake.on(MM_TOKEN["cn"], (200, {"access_token": "x", "base_resp": {"status_code": 1004}}))
    with pytest.raises(ai_oauth.NeedsLogin):
        ai_oauth.ensure_fresh(rec)


def test_transient_failure_retries_then_succeeds(fake):
    rec = signed_in_record(expires_in_ms=1000)
    fake.on(RFC_TOKEN, (503, {}), (429, {}), (200, {"access_token": "AT-NEW", "expires_in": 3600}))
    assert ai_oauth.ensure_fresh(rec)["oauth"]["access"] == "AT-NEW"
    assert len(fake.hits(RFC_TOKEN)) == 3


def test_transient_failure_keeps_a_still_valid_token_but_not_an_expired_one(fake):
    valid = signed_in_record(expires_in_ms=5 * 60 * 1000)
    fake.on(RFC_TOKEN, (503, {}))
    assert ai_oauth.ensure_fresh(valid)["oauth"]["access"] == "AT-OLD-SECRET"
    expired = signed_in_record(expires_in_ms=-1000)
    with pytest.raises(ai_oauth.NeedsLogin):
        ai_oauth.ensure_fresh(expired)


def test_signed_out_or_incomplete_record_needs_login_and_non_oauth_passes_through(fake):
    rec = signed_in_record()
    ai_oauth.logout(rec["id"])
    with pytest.raises(ai_oauth.NeedsLogin):
        ai_oauth.ensure_fresh(ai_providers.get(rec["id"]))
    plain = {"id": "x", "agent": "claude", "api_key": "k"}
    assert ai_oauth.ensure_fresh(plain) is plain and ai_oauth.ensure_fresh(None) is None
    assert fake.requests == []


def test_run_refreshes_before_spawn_and_refuses_to_spawn_when_it_cannot(
    fake, monkeypatch, tmp_path
):
    rec = signed_in_record(expires_in_ms=1000)
    ai_providers.set_active("claude", rec["id"])
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: {})
    spawned = []
    monkeypatch.setattr(ai_bridge.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    fake.on(RFC_TOKEN, (400, {"error": "invalid_grant"}))
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.run("claude", "fig.py", "p", str(tmp_path))
    assert (
        exc.value.code == "ai_endpoint_needs_login" and exc.value.params["endpoint"] == rec["label"]
    )
    assert spawned == []
    # 刷新发生在快照 / 登记之前：脚本不存在的报错排在它后面
    fake.on(RFC_TOKEN, (200, {"access_token": "AT-NEW", "expires_in": 3600}))
    with pytest.raises(RuntimeError, match="脚本不存在"):
        ai_bridge.run("claude", "fig.py", "p", str(tmp_path))
    assert ai_providers.get(rec["id"])["oauth"]["access"] == "AT-NEW"


def test_refresh_runs_outside_the_request_lock_and_dedupe_still_wins(fake, monkeypatch, tmp_path):
    """刷新要联网，不许在脚本锁 / 请求锁里做；刷不动时，已受理请求的重发仍按去重返回（ADR 0132）。"""
    rec = signed_in_record(expires_in_ms=1000)
    ai_providers.set_active("claude", rec["id"])
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: {})
    spawned = []
    monkeypatch.setattr(ai_bridge.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    held = []
    real = ai_oauth.ensure_fresh

    def probe(r):
        # RLock 没有 locked()（3.10–3.13）：从另一个线程试拿，拿得到 = run 这时没持锁
        got = []

        def try_take():
            ok = ai_bridge._REQUEST_LOCK.acquire(blocking=False)
            got.append(ok)
            if ok:
                ai_bridge._REQUEST_LOCK.release()

        t = threading.Thread(target=try_take)
        t.start()
        t.join()
        held.append(not got[0])
        return real(r)

    monkeypatch.setattr(ai_oauth, "ensure_fresh", probe)
    fake.on(RFC_TOKEN, (400, {"error": "invalid_grant"}))
    monkeypatch.setattr(
        ai_bridge, "find_request", lambda pk, rid: {"status": "ok", "session": "S1"}
    )
    with pytest.raises(ai_bridge.DuplicateRequest):
        ai_bridge.run("claude", "fig.py", "p", str(tmp_path), client_request_id="rid-1")
    assert held == [False] and spawned == []
    monkeypatch.setattr(ai_bridge, "find_request", lambda pk, rid: None)
    with pytest.raises(ai_bridge.AgentError) as exc:
        ai_bridge.run("claude", "fig.py", "p", str(tmp_path), client_request_id="rid-2")
    assert exc.value.code == "ai_endpoint_needs_login" and spawned == []


def test_the_error_funnel_maps_needs_login_to_409():
    from tavotto import app as m

    with m.app.test_request_context():
        resp = m._agent_error(ai_bridge.AgentError("ai_endpoint_needs_login", {"endpoint": "k"}))
    assert resp[1] == 409 and resp[0].get_json()["code"] == "ai_endpoint_needs_login"


# ---------------------------------------------------------------------------
# 注入：Bearer，不是 x-api-key
# ---------------------------------------------------------------------------
def test_spawn_overrides_for_oauth_uses_bearer_token_only(fake):
    rec = signed_in_record()
    args, env = ai_providers.spawn_overrides("claude", rec, "some-model")
    assert args == []
    assert env["ANTHROPIC_AUTH_TOKEN"] == "AT-OLD-SECRET"
    assert env["ANTHROPIC_BASE_URL"] == "https://api.example.invalid/coding"
    # x-api-key 会让部分网关 401；且不能让用户 shell 里继承来的真 Anthropic 密钥顺路发给第三方
    assert env["ANTHROPIC_API_KEY"] == ""
    assert "AT-OLD-SECRET" not in {v for k, v in env.items() if k != "ANTHROPIC_AUTH_TOKEN"}
    assert env["ANTHROPIC_MODEL"] == "some-model"


def test_spawn_overrides_for_a_plain_key_is_unchanged():
    rec = {"agent": "claude", "base_url": "https://gw.example", "api_key": "sk-abc", "label": "g"}
    _, env = ai_providers.spawn_overrides("claude", rec)
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-abc" and env["ANTHROPIC_API_KEY"] == "sk-abc"


def test_signed_out_record_injects_no_token(fake):
    rec = signed_in_record()
    ai_oauth.logout(rec["id"])
    _, env = ai_providers.spawn_overrides("claude", ai_providers.get(rec["id"]))
    assert "ANTHROPIC_AUTH_TOKEN" not in env and env["ANTHROPIC_API_KEY"] == ""


# ---------------------------------------------------------------------------
# 退出登录 / 保存通道
# ---------------------------------------------------------------------------
def test_logout_deletes_tokens_but_keeps_the_record_and_remembers_provider(fake):
    rec = signed_in_record("minimax", "global")
    ai_providers.save({"id": rec["id"], "label": "my minimax", "models": ["M-x"]})
    ai_oauth.logout(rec["id"])
    after = ai_providers.get(rec["id"])
    assert after["label"] == "my minimax" and after["models"] == ["M-x"]
    assert (
        after["auth"] == "oauth"
        and "access" not in after["oauth"]
        and "refresh" not in after["oauth"]
    )
    pub = ai_providers.public(after)
    assert pub["signed_in"] is False and pub["has_key"] is False
    assert (pub["oauth_provider"], pub["oauth_region"]) == ("minimax", "global")
    assert "AT-OLD-SECRET" not in config.config_path().read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        ai_oauth.logout("nope")
    ai_providers.save({"id": "plain", "label": "p", "agent": "claude", "api_key": "k"})
    with pytest.raises(ValueError):
        ai_oauth.logout("plain")


def test_relogin_keeps_user_edits_and_replaces_tokens(fake):
    signed_in_record()
    ai_providers.save(
        {"id": "rfcvendor-oauth", "label": "mine", "models": ["m1"], "default_model": "m1"}
    )
    fake.on(RFC_DEVICE, RFC_DEVICE_OK)
    fake.on(RFC_TOKEN, RFC_TOKENS)
    flow = ai_oauth.start("rfcvendor")
    wait_for(flow["flow_id"], {"signed_in"})
    rec = ai_providers.get("rfcvendor-oauth")
    assert rec["label"] == "mine" and rec["default_model"] == "m1"
    assert rec["oauth"]["access"] == "AT-RFC-SECRET"
    assert len([p for p in ai_providers.list_providers() if p["id"] == "rfcvendor-oauth"]) == 1


def test_save_through_the_ui_channel_cannot_write_or_wipe_tokens(fake):
    rec = signed_in_record()
    ai_providers.save(
        {"id": rec["id"], "label": "renamed", "auth": "oauth", "oauth": {"access": "FORGED"}}
    )
    after = ai_providers.get(rec["id"])
    assert after["label"] == "renamed" and after["oauth"]["access"] == "AT-OLD-SECRET"
    ai_providers.save(
        {
            "id": "fresh",
            "label": "f",
            "agent": "claude",
            "auth": "oauth",
            "oauth": {"access": "FORGED"},
        }
    )
    assert "auth" not in ai_providers.get("fresh") and "oauth" not in ai_providers.get("fresh")


def test_deleting_the_record_removes_the_tokens(fake):
    rec = signed_in_record()
    ai_providers.delete(rec["id"])
    assert "AT-OLD-SECRET" not in config.config_path().read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 入口可见性 / client_id
# ---------------------------------------------------------------------------
def test_production_table_has_only_minimax_and_no_client_id_yet():
    """出厂状态（不经 fixture 的补丁）：只有 MiniMax，client_id 还没申请到 → 入口不出现。"""
    assert ai_oauth.CLIENT_IDS == {"minimax": None}
    assert set(ai_oauth._SPECS) == {"minimax"}


def test_without_a_client_id_the_entry_is_unavailable_and_start_refuses(fake, monkeypatch):
    monkeypatch.delenv("TAVOTTO_OAUTH_CLIENT_ID_MINIMAX")
    monkeypatch.setitem(ai_oauth.CLIENT_IDS, "rfcvendor", None)
    monkeypatch.setitem(ai_oauth.CLIENT_IDS, "minimax", None)
    caps = ai_bridge.capabilities(refresh=True)
    assert {k: v["oauth_available"] for k, v in caps["oauth"].items()} == {
        "rfcvendor": False,
        "minimax": False,
    }
    with pytest.raises(ai_oauth.OAuthError) as exc:
        ai_oauth.start("minimax")
    assert exc.value.code == "oauth_unavailable"
    assert fake.requests == []
    monkeypatch.setenv("TAVOTTO_OAUTH_CLIENT_ID_MINIMAX", "  ")  # 空白不算数
    assert ai_oauth.available()["minimax"]["oauth_available"] is False
    monkeypatch.setenv("TAVOTTO_OAUTH_CLIENT_ID_MINIMAX", CID)
    assert ai_oauth.available()["minimax"]["oauth_available"] is True
    assert ai_oauth.available()["minimax"]["regions"] == ["cn", "global"]


def test_table_client_id_is_used_when_no_env_override(monkeypatch):
    monkeypatch.delenv("TAVOTTO_OAUTH_CLIENT_ID_MINIMAX", raising=False)
    monkeypatch.setitem(ai_oauth.CLIENT_IDS, "minimax", "table-id")
    assert ai_oauth.client_id("minimax") == "table-id"
    monkeypatch.setenv("TAVOTTO_OAUTH_CLIENT_ID_MINIMAX", "env-id")
    assert ai_oauth.client_id("minimax") == "env-id"


_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
#: 别家客户端的 client_id 的 SHA-256（只存摘要：这份测试文件本身也不抄它们）
_FOREIGN_CLIENT_ID_DIGESTS = {
    "9a51d8fba526c54bf355205a99c8325ec07a056024515f826987cb2a042a13ac",
    "fc4a96169fe01f75d65c97ae2801d04b63069e895aa9be580d939858646e9b60",
}


def test_no_uuid_shaped_client_id_constant_in_the_oauth_modules():
    """源码里不写任何别家的 client_id：`ai_oauth` 里一个 UUID 形状的串都不许有；
    整个 `src/tavotto` 里也不许出现那两个已知的别家 client_id（比对 SHA-256）。"""
    assert _UUID.findall((SRC / "engine" / "ai_oauth.py").read_text(encoding="utf-8")) == []
    for path in SRC.rglob("*.py"):
        for found in _UUID.findall(path.read_text(encoding="utf-8", errors="replace")):
            digest = hashlib.sha256(found.lower().encode()).hexdigest()
            assert digest not in _FOREIGN_CLIENT_ID_DIGESTS, f"{path} 含别家的 client_id"
    # 表里的值必须是 None 或非 UUID 串
    for value in ai_oauth.CLIENT_IDS.values():
        assert value is None or not _UUID.fullmatch(value)


def test_requests_never_claim_to_be_another_client(fake):
    fake.on(RFC_DEVICE, RFC_DEVICE_OK)
    fake.on(RFC_TOKEN, RFC_TOKENS)
    wait_for(ai_oauth.start("rfcvendor")["flow_id"], {"signed_in"})
    src = (SRC / "engine" / "ai_oauth.py").read_text(encoding="utf-8")
    assert not re.search(r"""["']kimi_cli["']""", src) and "X-Msh" not in src


# ---------------------------------------------------------------------------
# HTTP 端点 / 诊断包
# ---------------------------------------------------------------------------
@pytest.fixture
def http(fake):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    return m.app.test_client()


def test_http_flow_endpoints(fake, http):
    fake.on(RFC_DEVICE, RFC_DEVICE_OK)
    fake.on(RFC_TOKEN, RFC_PENDING, RFC_TOKENS)
    r = http.post("/api/ai/oauth/rfcvendor/start", json={})
    assert r.status_code == 200
    flow = r.get_json()
    assert flow["user_code"] == "RFC-1234" and "device_code" not in flow
    got = http.get(f"/api/ai/oauth/flows/{flow['flow_id']}")
    assert got.status_code == 200 and got.headers["Cache-Control"] == "no-store"
    wait_for(flow["flow_id"], {"signed_in"})
    caps = http.post("/api/ai/endpoints/rfcvendor-oauth/logout").get_json()
    row = next(e for e in caps["endpoints"] if e["id"] == "rfcvendor-oauth")
    assert row["signed_in"] is False and "oauth" not in row
    assert http.post("/api/ai/endpoints/rfcvendor-oauth/logout").status_code == 200
    bad = http.post("/api/ai/endpoints/nope/logout")
    assert bad.status_code == 400 and bad.get_json()["code"] == "endpoint_invalid"
    assert http.get("/api/ai/oauth/flows/nope").status_code == 404
    assert http.get("/api/ai/oauth/flows/nope").get_json()["code"] == "oauth_flow_not_found"
    assert http.post("/api/ai/oauth/flows/nope/cancel").status_code == 404


def test_http_cancel_and_errors(fake, http, monkeypatch):
    fake.on(RFC_DEVICE, RFC_DEVICE_OK)
    fake.on(RFC_TOKEN, RFC_PENDING)
    flow = http.post("/api/ai/oauth/rfcvendor/start", json={}).get_json()
    assert (
        http.post(f"/api/ai/oauth/flows/{flow['flow_id']}/cancel").get_json()["state"]
        == "cancelled"
    )
    assert (
        http.post("/api/ai/oauth/nobody/start", json={}).get_json()["code"] == "oauth_unavailable"
    )
    assert http.post("/api/ai/oauth/minimax/start", json={"region": "mars"}).status_code == 400
    fake.on(RFC_DEVICE, (500, {}))
    down = http.post("/api/ai/oauth/rfcvendor/start", json={})
    assert down.status_code == 502 and down.get_json()["code"] == "oauth_start_failed"
    monkeypatch.setitem(ai_oauth.CLIENT_IDS, "rfcvendor", None)
    no_id = http.post("/api/ai/oauth/rfcvendor/start", json={})
    assert no_id.status_code == 400 and no_id.get_json()["code"] == "oauth_unavailable"


def test_tokens_are_in_neither_the_diagnostics_report_nor_the_exported_bundle(fake, http):
    import zipfile
    from io import BytesIO

    signed_in_record()
    signed_in_record("minimax", "cn")
    ai_bridge.invalidate_capabilities()
    blobs = []
    for data in (
        http.get("/api/diagnostics/bundle").data,
        http.post("/api/diagnostics/bundle", json={}).data,
    ):
        z = zipfile.ZipFile(BytesIO(data))
        blobs.append("\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist()))
        report = json.loads(z.read("report.json"))
        assert {e["id"] for e in report["ai_endpoints"]} >= {"rfcvendor-oauth", "minimax-oauth"}
    for blob in blobs:
        assert "AT-OLD-SECRET" not in blob and "RT-OLD-SECRET" not in blob


def test_tokens_never_appear_in_the_capabilities_json(fake, http):
    signed_in_record()
    body = http.get("/api/ai/capabilities?refresh=1").get_data(as_text=True)
    assert (
        "AT-OLD-SECRET" not in body and "RT-OLD-SECRET" not in body and "token_endpoint" not in body
    )


def test_oauth_commit_interleaved_with_an_endpoint_save_keeps_both_changes(fake, monkeypatch):
    """Codex #1326：账号登录写令牌读完接口列表后，另一个请求保存了别的接口——两边的改动都得留在配置里。
    （读-改-写在同一个 `config.update_ai_settings` 事务里，后来者等锁，不再整份覆盖。）"""
    ai_providers.save({"id": "other", "label": "Old", "agent": "claude", "base_url": "http://x"})
    fired = []
    real_clean = ai_providers._clean

    def interleaving_clean(rec, existing_ids=frozenset()):
        if rec.get("auth") == "oauth" and not fired:
            fired.append(1)  # store_oauth 已经读过列表：此刻并发一次保存
            t = threading.Thread(target=lambda: ai_providers.save({"id": "other", "label": "New"}))
            t.start()
            t.join(0.3)  # 事务串行时它在等锁（超时返回）；不串行时它已写完，随后被盖掉
            interleaving_clean.thread = t
        return real_clean(rec, existing_ids)

    monkeypatch.setattr(ai_providers, "_clean", interleaving_clean)
    signed_in_record()
    interleaving_clean.thread.join(5)
    assert not interleaving_clean.thread.is_alive()
    assert ai_providers.get("other")["label"] == "New"
    assert (ai_providers.get("rfcvendor-oauth")["oauth"] or {}).get("access") == "AT-OLD-SECRET"
