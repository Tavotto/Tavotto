"""高级版授权客户端（ADR 0193 §四 / §五 / §七）：设备签名与服务端固定向量对拍、令牌验签、零网络、失败码映射、本机路由。

出站全部打桩 `diagsend._send`（没有任何真网络）；「服务端」是本文件里的 `FakeServer`：按 README 的规则验设备签名、用测试密钥签令牌。
会话认证对每条路由的默认拒绝由 `test_browser_auth.py` 从 url_map 枚举（这里再钉“这些路由确实在 url_map 里”）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import time

import pytest

from tavotto import app as m
from tavotto.engine import diagnostictext, diagsend, license as lic, reviewshare

nacl_signing = pytest.importorskip("nacl.signing")

# serverless/license/test/license.test.ts 的固定向量（README「设备签名」一节同值）
VECTOR_BODY = {
    "code": "TVK-AAAA-BBBB-CCCC",
    "op": "activate",
    "device_pub": "AAAA",
    "ts": 1_700_000_000_000,
    "n": {"b": 1, "a": "é"},
}
VECTOR_CANON = (
    '{"code":"TVK-AAAA-BBBB-CCCC","device_pub":"AAAA","n":{"a":"é","b":1},'
    '"op":"activate","ts":1700000000000}'
)
VECTOR_SHA = "c95af6d458b77e912bbf42a8eb51bfbd9ccd2e64a6d20f8259fc1a001c319dc4"

ENDPOINT = "https://license.tavotto.cn"
KID = "test-1"
LID = "lic_abcdefghijklmn"
CODE = "TVK-ABCD-EFGH-JKMN"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


class FakeServer:
    """按 serverless/license 的规则回应；记录每一次请求。`script` 里排队的 `(status, body)` 优先于正常逻辑。"""

    def __init__(self, now=time.time):
        self.signing = nacl_signing.SigningKey.generate()
        self.now = now
        self.calls: list[tuple[str, dict]] = []
        self.script: list[object] = []
        self.features = ["review-plus"]
        self.exp = int(now()) + 30 * 86400
        self.offline_days = 14
        self.extra: dict = {}  # 额外的令牌键（创始标记等）

    @property
    def pubkey_b64(self) -> str:
        return _b64(bytes(self.signing.verify_key))

    def mint(self, device_pub_b64: str, **over) -> dict:
        iat = int(self.now())
        payload = {
            "v": 1,
            "lid": LID,
            "features": self.features,
            "exp": self.exp,
            "iat": iat,
            "device": hashlib.sha256(base64.b64decode(device_pub_b64)).hexdigest(),
            "refresh_after": iat + 86400,
            "offline_until": min(self.exp, iat + self.offline_days * 86400),
            "email_hint": "m***@gmail.com",
            **self.extra,
            **over,
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return {
            "payload": _b64(raw),
            "sig": _b64(self.signing.sign(raw).signature),
            "kid": KID,
        }

    def _check_sig(self, path: str, body: dict, op: str) -> None:
        assert body["op"] == op, f"op 与端点不一致: {path}"
        pub = base64.b64decode(body["device_pub"])
        nacl_signing.VerifyKey(pub).verify(lic.signed_bytes(body), base64.b64decode(body["sig"]))
        assert abs(self.now() * 1000 - body["ts"]) < 600_000

    def __call__(self, req, timeout, limit=0):
        path = req.full_url.removeprefix(ENDPOINT)
        body = json.loads(req.data.decode())
        self.calls.append((path, body))
        assert req.get_method() == "POST"
        if self.script:
            status, payload = self.script.pop(0)
            if isinstance(status, Exception):
                raise status
            return diagsend._Response(status, {}, json.dumps(payload).encode())
        if path == "/v1/activate":
            self._check_sig(path, body, "activate")
            return self._ok({"token": self.mint(body["device_pub"])})
        if path == "/v1/refresh":
            self._check_sig(path, body, "refresh")
            return self._ok({"token": self.mint(body["device_pub"])})
        if path == "/v1/devices/release":
            self._check_sig(path, body, "release")
            return self._ok({"ok": True})
        if path == "/v1/checkout":
            if "renew" in body:
                self._check_sig(path, body["renew"], "renew")
            return self._ok({"url": "https://checkout.stripe.com/c/pay/cs_test_x"})
        raise AssertionError(path)

    @staticmethod
    def _ok(payload):
        return diagsend._Response(200, {}, json.dumps(payload).encode())


def err(status: int, code: str):
    return (status, {"error": {"code": code, "message": "x"}})


@pytest.fixture
def server(monkeypatch, tmp_path):
    for name in (lic.ENV_HARD_OFF, diagsend.ENV_DEV_LOOPBACK):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv(lic.ENV_ENDPOINT, ENDPOINT)
    srv = FakeServer(now=lambda: lic._time())
    monkeypatch.setenv(lic.ENV_TEST_PUBKEY, srv.pubkey_b64)
    monkeypatch.setattr(diagsend, "_send", srv)
    lic.reset_for_tests()
    yield srv
    lic.reset_for_tests()


@pytest.fixture
def clock(monkeypatch):
    t = {"now": time.time()}
    monkeypatch.setattr(lic, "_time", lambda: t["now"])
    return t


def activated(server):
    lic.activate(CODE)
    server.calls.clear()


# ---------------------------------------------------------------------------
# 签名：与服务端固定向量逐字节对拍
# ---------------------------------------------------------------------------
def test_canonical_json_and_signed_bytes_match_server_vector():
    assert lic.canonicalize({k: v for k, v in VECTOR_BODY.items()}) == VECTOR_CANON
    msg = lic.signed_bytes({**VECTOR_BODY, "sig": "ignored"})
    assert msg == f"1700000000000\n{VECTOR_SHA}".encode()
    assert hashlib.sha256(VECTOR_CANON.encode()).hexdigest() == VECTOR_SHA
    assert lic.signed_bytes({**VECTOR_BODY, "sig": "other"}) == msg  # sig 不参与


def test_second_vector_computed_by_the_real_server_code():
    """用服务端 `src/devicesig.ts` 的 `signedBytes` 实算（node 26）的边角向量：转义、DEL、U+2028、代理对、UTF-16 键序、嵌套数组。"""
    body = {
        "license_id": "lic_abcdefghijklmn",
        "op": "refresh",
        "device_pub": "AAAA",
        "ts": 1_700_000_000_123,
        "s": 'é\n"\x7f\u2028\U0001f600',
        "n": {"z": [1, True, None], "\U0001f600": 1, "\uff5e": 2},
    }
    assert lic.signed_bytes(body) == (
        b"1700000000123\nbe555c698159043c6401d1301c048904be739c40977bc0f2d54430455487f92c"
    )


def test_canonical_json_rules():
    assert (
        lic.canonicalize({"b": [True, None, 'x\n"é'], "a": 0})
        == '{"a":0,"b":[true,null,"x\\n\\"é"]}'
    )
    # UTF-16 码元序：U+FF5E（BMP）排在 U+1F600（代理对 D83D…）之后
    assert lic.canonicalize({"～": 1, "\U0001f600": 2}) == '{"\U0001f600":2,"～":1}'
    for bad in (1.5, float("nan"), 2**53, object()):
        with pytest.raises(ValueError):
            lic.canonicalize({"x": bad})


def test_sign_body_is_verifiable_and_op_is_closed(server):
    body = lic.sign_body({"op": "refresh", "ts": 1_700_000_000_000, "license_id": LID})
    pub = base64.b64decode(body["device_pub"])
    assert len(pub) == 32
    nacl_signing.VerifyKey(pub).verify(lic.signed_bytes(body), base64.b64decode(body["sig"]))
    with pytest.raises(diagsend._Failure):
        lic.sign_body({"op": "recover", "ts": 1})


def test_device_key_is_0600_in_0700_dir_and_not_the_review_key(server):
    lic.sign_body({"op": "refresh", "ts": 1})
    p = lic._key_path()
    assert len(p.read_bytes()) == 32
    if os.name != "nt":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
        assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700
    assert p.parent.name == "license"
    assert p != lic.config.data_path("review", reviewshare.KEY_FILE)
    first = p.read_bytes()
    lic.sign_body({"op": "refresh", "ts": 2})
    assert p.read_bytes() == first  # 第二次不换密钥


# ---------------------------------------------------------------------------
# 令牌：验签 / 篡改 / kid / 设备 / 时间
# ---------------------------------------------------------------------------
def test_has_true_for_a_valid_token_and_only_for_its_features(server):
    activated(server)
    assert lic.has("review-plus") is True
    assert lic.has("hosted-ai") is False  # 令牌里没有
    assert lic.has("not-registered") is False  # 不在闭集
    assert lic.has(None) is False  # type: ignore[arg-type]
    st = lic.status()
    assert st["licensed"] and st["state"] == "active" and st["features"] == ["review-plus"]


def test_unregistered_feature_in_token_is_false_even_if_signed(server):
    server.features = ["review-plus", "time-travel"]
    activated(server)
    assert lic.has("time-travel") is False
    assert lic.status()["features"] == ["review-plus"]


def _rewrite(env_mutator):
    rec = json.loads(lic._token_path().read_text())
    env_mutator(rec["token"])
    lic._token_path().write_text(json.dumps(rec))


def test_tampered_payload_fails(server):
    activated(server)

    def tamper(env):
        raw = json.loads(base64.b64decode(env["payload"]))
        raw["features"] = ["hosted-ai", "review-plus"]
        env["payload"] = _b64(json.dumps(raw, separators=(",", ":")).encode())

    _rewrite(tamper)
    assert lic.has("review-plus") is False and lic.has("hosted-ai") is False


def test_tampered_signature_fails(server):
    activated(server)

    def tamper(env):
        sig = bytearray(base64.b64decode(env["sig"]))
        sig[0] ^= 1
        env["sig"] = _b64(bytes(sig))

    _rewrite(tamper)
    assert lic.has("review-plus") is False


def test_unknown_kid_fails(server, monkeypatch):
    activated(server)
    # 测试服务端的 kid 是部署时随意配的（k-test / local …），所以测试公钥对「内置表里没有的任何 kid」都认——但只在设了
    # 合规端点 + 测试公钥时；没有测试公钥，未知 kid 一律不认
    monkeypatch.delenv(lic.ENV_TEST_PUBKEY)
    _rewrite(lambda env: env.update(kid="prod-9"))
    assert lic.has("review-plus") is False
    monkeypatch.setenv(lic.ENV_TEST_PUBKEY, "not base64!")
    assert lic.has("review-plus") is False
    monkeypatch.setenv(lic.ENV_TEST_PUBKEY, _b64(b"short"))
    assert lic.has("review-plus") is False


def test_token_for_another_device_fails(server):
    other = nacl_signing.SigningKey.generate()
    env = server.mint(_b64(bytes(other.verify_key)))
    lic.sign_body({"op": "refresh", "ts": 1})  # 生成本机设备密钥
    lic._save(env, "ok")
    assert lic.has("review-plus") is False
    assert lic.status()["state"] == "lapsed"


def test_past_offline_until_and_past_exp_fail(server, clock):
    activated(server)
    assert lic.has("review-plus") is True
    clock["now"] += 14 * 86400 + 5  # 过了断网宽限
    assert lic.has("review-plus") is False
    st = lic.status()
    assert st["state"] == "lapsed" and st["needs_refresh"] is True and not st["licensed"]
    clock["now"] = server.exp + 5
    assert lic.has("review-plus") is False
    assert lic.status()["state"] == "expired"


def test_garbage_token_file_never_raises(server):
    lic._token_path().parent.mkdir(parents=True, exist_ok=True)
    for junk in (
        b"",
        b"{",
        b"[]",
        b'{"token": 5}',
        b'{"token": {"payload": 1, "sig": 2, "kid": 3}}',
    ):
        lic._token_path().write_bytes(junk)
        assert lic.has("review-plus") is False
        assert lic.status()["licensed"] is False


def test_test_pubkey_is_ignored_without_an_endpoint_override(server, monkeypatch):
    activated(server)
    assert lic.has("review-plus") is True
    monkeypatch.delenv(lic.ENV_ENDPOINT)
    monkeypatch.setattr(lic, "PRODUCTION_LIVE", True)
    assert lic.enabled() is True
    assert lic.has("review-plus") is False  # 正式安装不认测试服务端签的令牌


def test_builtin_public_keys_take_precedence_over_the_test_key(server, monkeypatch):
    activated(server)
    monkeypatch.setitem(lic.PUBLIC_KEYS, KID, bytes(nacl_signing.SigningKey.generate().verify_key))
    assert lic.has("review-plus") is False
    monkeypatch.setitem(lic.PUBLIC_KEYS, KID, bytes(server.signing.verify_key))
    assert lic.has("review-plus") is True


def test_production_public_keys_are_pinned():
    # 只有正式 kid；测试密钥的公钥绝不进这张表（ADR 0193 §七）。换 / 加 kid 要同步 ADR 与发版
    assert set(lic.PUBLIC_KEYS) == {"live-2026-11"}
    assert all(len(v) == 32 for v in lic.PUBLIC_KEYS.values())
    assert (
        lic.PUBLIC_KEYS["live-2026-11"].hex()
        == "c4e27e8827e05bd65a60243f3e176dfa93fe923ff8ee91d7b8937be1e8857056"
    )


# ---------------------------------------------------------------------------
# 零网络与开关
# ---------------------------------------------------------------------------
def test_no_token_means_zero_network(server):
    assert lic.status()["state"] == "none"
    assert lic.has("review-plus") is False
    lic.maybe_refresh()
    assert lic.refresh()["refreshed"] is False
    assert lic.refresh(force=True)["refreshed"] is False
    assert lic.release()["ok"] is True
    with pytest.raises(lic.LicenseError) as e:
        lic.checkout("pro_year", "zh", renew=True)
    assert e.value.code == "client_error"
    assert server.calls == []
    assert not lic._key_path().exists()  # 连设备密钥都不生成


def test_hard_off_and_not_live_mean_zero_network_and_disabled(server, monkeypatch):
    activated(server)
    monkeypatch.setenv(lic.ENV_HARD_OFF, "1")
    assert lic.enabled() is False and lic.status() == {"enabled": False}
    assert lic.has("review-plus") is False
    for call in (lambda: lic.activate(CODE), lic.refresh, lic.release, lambda: lic.checkout("k")):
        with pytest.raises(lic.LicenseError) as e:
            call()
        assert e.value.code == "disabled" and e.value.status == 404
    lic.maybe_refresh()
    assert server.calls == []

    monkeypatch.delenv(lic.ENV_HARD_OFF)
    monkeypatch.delenv(lic.ENV_ENDPOINT)
    assert lic.PRODUCTION_LIVE is False
    assert lic.enabled() is False and lic.status() == {"enabled": False}
    assert lic.has("review-plus") is False
    with pytest.raises(lic.LicenseError):
        lic.activate(CODE)
    assert server.calls == []


@pytest.mark.parametrize(
    "raw",
    [
        "https://evil.example",
        "https://license.tavotto.cn.evil.example",
        "http://license.tavotto.cn",
        "https://license.tavotto.cn/v1",
        "https://user@license.tavotto.cn",
        "https://127.0.0.1:8788",  # 回环没开开发模式
        "https://tavotto.cn",
    ],
)
def test_noncompliant_endpoint_disables_without_falling_back(server, monkeypatch, raw):
    monkeypatch.setattr(lic, "PRODUCTION_LIVE", True)
    monkeypatch.setenv(lic.ENV_ENDPOINT, raw)
    assert lic.resolve_target() is None and lic.enabled() is False
    with pytest.raises(lic.LicenseError):
        lic.activate(CODE)
    assert server.calls == []


def test_live_flag_with_builtin_endpoint(monkeypatch):
    monkeypatch.delenv(lic.ENV_ENDPOINT, raising=False)
    monkeypatch.delenv(lic.ENV_HARD_OFF, raising=False)
    assert lic.resolve_target() is None  # PRODUCTION_LIVE = False
    monkeypatch.setattr(lic, "PRODUCTION_LIVE", True)
    t = lic.resolve_target()
    assert t is not None and t.host == "license.tavotto.cn" and t.base == ENDPOINT
    assert lic.LICENSE_HOSTS == frozenset({"license.tavotto.cn"})


def test_dev_loopback_allows_http_loopback_only_with_the_flag(monkeypatch):
    monkeypatch.delenv(lic.ENV_HARD_OFF, raising=False)
    monkeypatch.setenv(lic.ENV_ENDPOINT, "http://127.0.0.1:8788")
    assert lic.resolve_target() is None
    monkeypatch.setenv(diagsend.ENV_DEV_LOOPBACK, "1")
    t = lic.resolve_target()
    assert t is not None and t.base == "http://127.0.0.1:8788"
    monkeypatch.setenv(lic.ENV_ENDPOINT, "http://evil.example:8788")
    assert lic.resolve_target() is None


# ---------------------------------------------------------------------------
# 激活 / 刷新 / 解绑 / 付款
# ---------------------------------------------------------------------------
def test_activate_persists_token_and_signs_the_right_op(server):
    out = lic.activate(" tvk-abcd-efgh-jkmn ")
    assert out["ok"] and out["licensed"] and out["email_hint"] == "m***@gmail.com"
    ((path, body),) = server.calls
    assert path == "/v1/activate" and body["code"] == CODE and body["op"] == "activate"
    assert set(body) == {"code", "op", "device_pub", "ts", "sig"}
    assert lic.has("review-plus") is True
    rec = json.loads(lic._token_path().read_text())
    assert set(rec["token"]) == {"payload", "sig", "kid"} and rec["state"] == "ok"
    if os.name != "nt":
        assert stat.S_IMODE(lic._token_path().stat().st_mode) == 0o600
    # status 不含令牌原文与设备密钥
    blob = json.dumps(lic.status())
    assert rec["token"]["sig"] not in blob and rec["token"]["payload"] not in blob
    assert _b64(lic._key_path().read_bytes()) not in blob


@pytest.mark.parametrize(
    ("status", "code", "want"),
    [
        (404, "invalid_code", "invalid_code"),
        (409, "device_limit", "device_limit"),
        (403, "revoked", "revoked"),
        (403, "expired", "expired"),
        (422, "price_unavailable", "price_unavailable"),
        (429, "rate_limited", "rate_limited"),
        (500, "server", "server"),
        (401, "client_error", "client_error"),
        (400, "client_error", "client_error"),
        (404, "", "server"),  # 裸 404 = 网关问题
        (502, "", "server"),
        (429, "", "rate_limited"),
        (418, "", "client_error"),
        (400, "network", "client_error"),  # network 是客户端自己判的，服务端发了也不认
        (400, "made_up", "client_error"),
    ],
)
def test_activate_failure_mapping(server, status, code, want):
    server.script = [(status, {"error": {"code": code}} if code else {})]
    with pytest.raises(lic.LicenseError) as e:
        lic.activate(CODE)
    assert e.value.code == want and want in lic.FAILURES
    assert lic.has("review-plus") is False and not lic._token_path().exists()


@pytest.mark.parametrize("code", ["network", "timeout", "tls"])
def test_transport_failures_are_network(server, code):
    server.script = [(diagsend._Failure(code), None)]
    with pytest.raises(lic.LicenseError) as e:
        lic.activate(CODE)
    assert e.value.code == "network" and e.value.status == 502


def test_bad_responses_are_server_errors_and_never_saved(server):
    other = nacl_signing.SigningKey.generate()
    bad_tokens = [
        {},  # 没有 token
        {"token": "x"},
        {"token": server.mint(_b64(bytes(other.verify_key)))},  # 不是这台设备
    ]
    for body in bad_tokens:
        server.script = [(200, body)]
        with pytest.raises(lic.LicenseError) as e:
            lic.activate(CODE)
        assert e.value.code == "server"
        assert not lic._token_path().exists()


def test_activate_rejects_junk_codes_without_network(server):
    for junk in (None, 5, "", "   ", "x" * 100, "码"):
        with pytest.raises(lic.LicenseError) as e:
            lic.activate(junk)
        assert e.value.code == "invalid_code"
    assert server.calls == []


def test_refresh_is_throttled(server, clock):
    activated(server)
    # 还没到 refresh_after：零网络
    assert lic.refresh()["refreshed"] is False
    lic.maybe_refresh()
    assert server.calls == []
    clock["now"] += 86400 + 10
    assert lic.status()["needs_refresh"] is True
    lic.maybe_refresh()
    assert [p for p, _ in server.calls] == ["/v1/refresh"]
    assert server.calls[0][1]["license_id"] == LID and server.calls[0][1]["op"] == "refresh"
    # 新令牌的 refresh_after 又是一天后；启动期间再调也不再联网
    lic.maybe_refresh()
    clock["now"] += 86400 + 10
    lic.maybe_refresh()  # 每天最多一次（本进程内已有一次尝试）：距上次不满一天时不发；满一天才发
    assert len(server.calls) == 2


def test_failed_refresh_keeps_the_token_and_is_not_retried_in_a_loop(server, clock):
    activated(server)
    clock["now"] += 86400 + 10
    server.script = [(diagsend._Failure("network"), None)]
    lic.maybe_refresh()
    assert len(server.calls) == 1
    assert lic.has("review-plus") is True  # 断网宽限内照常
    lic.maybe_refresh()  # 当天不再试
    assert len(server.calls) == 1
    server.script = [(diagsend._Failure("network"), None)]
    assert lic.refresh(force=True)["refreshed"] is False  # 手动刷新有 60 秒的最小间隔：不发、不抛
    assert len(server.calls) == 1
    clock["now"] += 61
    with pytest.raises(lic.LicenseError) as e:
        lic.refresh(force=True)
    assert e.value.code == "network" and len(server.calls) == 2


@pytest.mark.parametrize(
    "how", ["state_revoked", "state_expired", "error_revoked", "error_expired"]
)
def test_revoked_or_expired_from_server_kills_the_token_but_keeps_the_device_key(
    server, clock, how
):
    activated(server)
    key_before = lic._key_path().read_bytes()
    clock["now"] += 86400 + 10
    verdict = how.split("_")[1]
    server.script = [(200, {"state": verdict}) if how.startswith("state") else err(403, verdict)]
    try:
        out = lic.refresh()
        assert out["refreshed"] is False
    except lic.LicenseError as e:
        assert e.code == verdict
    assert lic.has("review-plus") is False
    assert lic.status()["state"] == verdict and lic.status()["licensed"] is False
    assert lic._key_path().read_bytes() == key_before
    n = len(server.calls)
    clock["now"] += 3 * 86400
    if verdict == "revoked":
        # 作废后不再联网刷新，只能重新激活
        lic.maybe_refresh()
        lic.refresh(force=True)
        assert len(server.calls) == n
    else:
        # 过期后仍每天试一次刷新（续费后要靠它拿新令牌），没拿到之前权益照旧关着
        server.script = [(200, {"state": "expired"})]
        lic.maybe_refresh()
        assert len(server.calls) == n + 1
        assert lic.has("review-plus") is False


def test_expired_then_renewed_then_refresh_restores_the_license(server, clock):
    """过期 → 续费付款（服务端顺延）→ 刷新：不用重新输激活码就拿回权益。"""
    activated(server)
    clock["now"] += 86400 + 10
    server.script = [(200, {"state": "expired"})]
    lic.refresh()
    assert lic.has("review-plus") is False and lic.status()["state"] == "expired"
    # 续费付款完成后，服务端给出新令牌（fake 默认签一张新的）
    clock["now"] += 61
    out = lic.refresh(force=True)
    assert out["refreshed"] is True
    assert lic.has("review-plus") is True and lic.status()["state"] == "active"


def test_reactivating_after_revoked_works(server, clock):
    activated(server)
    clock["now"] += 86400 + 10
    server.script = [(200, {"state": "revoked"})]
    lic.refresh()
    assert lic.has("review-plus") is False
    lic.activate(CODE)
    assert lic.has("review-plus") is True


def test_refresh_with_a_foreign_license_id_is_rejected(server, clock):
    activated(server)
    clock["now"] += 86400 + 10
    server.script = [
        (
            200,
            {
                "token": server.mint(
                    base64.b64encode(
                        bytes(nacl_signing.SigningKey(lic._key_path().read_bytes()).verify_key)
                    ).decode(),
                    lid="lic_zzzzzzzzzzzzzz",
                )
            },
        )
    ]
    with pytest.raises(lic.LicenseError) as e:
        lic.refresh()
    assert e.value.code == "server"


def test_release_unbinds_and_removes_the_token_but_keeps_the_key(server):
    activated(server)
    out = lic.release()
    assert out["ok"] and out["licensed"] is False
    ((path, body),) = server.calls
    assert path == "/v1/devices/release" and body["op"] == "release" and body["license_id"] == LID
    assert not lic._token_path().exists() and lic._key_path().exists()
    assert lic.has("review-plus") is False


def test_release_failure_keeps_the_token(server):
    activated(server)
    server.script = [(diagsend._Failure("network"), None)]
    with pytest.raises(lic.LicenseError):
        lic.release()
    assert lic.has("review-plus") is True


def test_checkout_new_purchase_has_no_renew_object(server):
    out = lic.checkout("pro_year", "en")
    assert out == {"ok": True, "url": "https://checkout.stripe.com/c/pay/cs_test_x"}
    ((path, body),) = server.calls
    assert path == "/v1/checkout" and body == {"lookup_key": "pro_year", "locale": "en"}
    assert not lic._key_path().exists()  # 新购不需要设备密钥、不要求有令牌


def test_checkout_renew_carries_a_signed_renew_object(server, clock):
    activated(server)
    clock["now"] = server.exp + 100  # 过期了也能续费
    out = lic.checkout("pro_year", "zh", renew=True)
    assert out["ok"]
    ((_, body),) = server.calls
    r = body["renew"]
    assert (
        r["op"] == "renew"
        and r["license_id"] == LID
        and set(r) == {"op", "license_id", "device_pub", "ts", "sig"}
    )
    assert body["lookup_key"] == "pro_year"


@pytest.mark.parametrize("lookup_key", [None, "", "a b", "x" * 101, "../x", 5])
def test_checkout_bad_lookup_key_is_client_error(server, lookup_key):
    with pytest.raises(lic.LicenseError) as e:
        lic.checkout(lookup_key)
    assert e.value.code == "client_error" and server.calls == []


def test_checkout_unknown_locale_falls_back_and_price_unavailable_maps(server):
    server.script = [err(422, "price_unavailable")]
    with pytest.raises(lic.LicenseError) as e:
        lic.checkout("pro", "zh-CN-x-evil")
    assert e.value.code == "price_unavailable"
    assert server.calls[0][1]["locale"] == "zh"


@pytest.mark.parametrize(
    "url",
    [
        "http://checkout.stripe.com/x",
        "https://evil.example/x",
        "https://checkout.stripe.com.evil.example/x",
        "https://u@checkout.stripe.com/x",
        "javascript:alert(1)",
        "",
        None,
        5,
    ],
)
def test_checkout_url_must_be_a_stripe_https_page(server, url):
    server.script = [(200, {"url": url})]
    with pytest.raises(lic.LicenseError) as e:
        lic.checkout("pro")
    assert e.value.code == "server"


def test_failure_codes_are_the_adr_closed_set():
    assert set(lic.FAILURES) == {
        "invalid_code", "device_limit", "revoked", "expired", "price_unavailable",
        "rate_limited", "network", "server", "client_error",
    }  # fmt: skip
    assert set(lic.HTTP_STATUS) == set(lic.FAILURES)
    assert lic.OPS == {
        "activate",
        "refresh",
        "release",
        "renew",
        "pro_manifest",
    }  # pro_manifest：ADR 0198 §五
    assert lic.FEATURES == {"hosted-ai", "review-plus", "quick-layout"}  # quick-layout：ADR 0199


def test_background_refresh_thread_is_a_noop_when_disabled(monkeypatch):
    monkeypatch.setenv(lic.ENV_HARD_OFF, "1")
    lic.reset_for_tests()
    lic.start_background_refresh()
    assert not lic._started.is_set()


# ---------------------------------------------------------------------------
# 诊断包与脱敏
# ---------------------------------------------------------------------------
def test_scrub_covers_activation_codes_and_token_payloads(server):
    activated(server)
    env = json.loads(lic._token_path().read_text())["token"]
    for text in (
        f"activate failed for {CODE} twice",
        "code tvk-abcd-efgh-jkmn!",
        f'{{"payload":"{env["payload"]}"}}',
    ):
        out = diagnostictext.redact_text(text)
        assert "ABCD-EFGH" not in out.upper() and env["payload"] not in out, out
        assert "***" in out
    # 反证：普通文字不受影响
    assert diagnostictext.redact_text("TVK is a prefix; TVK-1 too") == "TVK is a prefix; TVK-1 too"


def test_license_dir_never_reaches_the_diagnostics_bundle(server, monkeypatch):
    import io
    import zipfile

    activated(server)
    secrets = [
        CODE,
        json.loads(lic._token_path().read_text())["token"]["payload"],
        _b64(lic._key_path().read_bytes()),
        lic._key_path().read_bytes().hex(),
        "m***@gmail.com",
    ]
    m.app.config["TESTING"] = True
    m.reset_projects()
    res = m.app.test_client().get("/api/diagnostics/bundle")
    assert res.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(res.data))
    assert z.namelist()
    for name in z.namelist():
        assert "license" not in name.lower() or name.endswith("README.md") is False
        body = z.read(name)
        for s in secrets:
            assert s.encode() not in body, f"{name} 带出了授权信息"
        assert lic._key_path().read_bytes() not in body


# ---------------------------------------------------------------------------
# 本机路由
# ---------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    m.app.config["TESTING"] = True
    m.reset_projects()
    return m.app.test_client()


ROUTES = [
    ("GET", "/api/license/status"),
    ("POST", "/api/license/activate"),
    ("POST", "/api/license/refresh"),
    ("POST", "/api/license/release"),
    ("POST", "/api/license/checkout"),
]


def test_routes_exist_in_the_url_map_so_the_auth_enumeration_covers_them():
    have = {(meth, str(r.rule)) for r in m.app.url_map.iter_rules() for meth in r.methods}
    for route in ROUTES:
        assert route in have
    assert not any("/api/license" in p for p in m.security._PUBLIC_PATHS)


def test_routes_when_disabled(client, server, monkeypatch):
    monkeypatch.setenv(lic.ENV_HARD_OFF, "1")
    assert client.get("/api/license/status").get_json() == {"enabled": False}
    for method, path in ROUTES[1:]:
        res = client.open(path, method=method, json={})
        assert res.status_code == 404
        assert res.get_json()["enabled"] is False
    assert server.calls == []


def test_routes_end_to_end(client, server):
    assert client.get("/api/license/status").get_json()["state"] == "none"
    res = client.post("/api/license/activate", json={"code": CODE})
    body = res.get_json()
    assert (
        res.status_code == 200
        and body["ok"]
        and body["licensed"]
        and body["features"] == ["review-plus"]
    )
    assert "payload" not in json.dumps(body)
    assert client.get("/api/license/status").get_json()["licensed"] is True
    res = client.post("/api/license/refresh", json={})
    assert (
        res.status_code == 200 and res.get_json()["refreshed"] is False
    )  # 路由的刷新 force 只跳过 refresh_after；60 秒内刚尝试过（activate）不再发
    res = client.post(
        "/api/license/checkout", json={"lookup_key": "pro", "locale": "en", "renew": True}
    )
    assert res.get_json()["url"].startswith("https://checkout.stripe.com/")
    assert "renew" in server.calls[-1][1]
    res = client.post("/api/license/release", json={})
    assert res.status_code == 200 and res.get_json()["licensed"] is False


def test_route_failures_use_the_closed_codes(client, server):
    server.script = [err(409, "device_limit")]
    res = client.post("/api/license/activate", json={"code": CODE})
    assert res.status_code == 409
    assert res.get_json() == {"ok": False, "error": "device_limit", "code": "license_device_limit"}
    res = client.post("/api/license/activate", json={"code": 5})
    assert res.status_code == 400 and res.get_json()["error"] == "invalid_code"
    res = client.post("/api/license/checkout", json={})
    assert res.get_json()["error"] == "client_error"


# ---------------------------------------------------------------------------
# 创始会员标记（founding_no / founding_since）
# ---------------------------------------------------------------------------
FOUNDING = {"founding_no": 37, "founding_since": 1_763_000_000}


def test_status_has_no_founding_for_a_regular_license(server):
    activated(server)
    assert "founding" not in lic.status()


def test_status_carries_founding_when_the_signed_token_has_it(server):
    server.extra = dict(FOUNDING)
    activated(server)
    st = lic.status()
    assert st["founding"] == {"no": 37, "since": 1_763_000_000, "name": ""}
    assert st["state"] == "active"

    # 创始编号只出现在 founding 里：逐个字段比值，不用子串（时间戳 / 令牌里碰巧有「37」会假红）
    def leaves(v):
        if isinstance(v, dict):
            for x in v.values():
                yield from leaves(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                yield from leaves(x)
        else:
            yield v

    rest = [x for k, v in st.items() if k != "founding" for x in leaves(v)]
    assert not any(x == 37 and not isinstance(x, bool) for x in rest)
    assert 1_763_000_000 not in rest


def test_founding_survives_expiry_and_lapse_but_not_revocation(server, clock):
    server.extra = dict(FOUNDING)
    activated(server)
    clock["now"] += 14 * 86400 + 5  # lapsed
    st = lic.status()
    assert st["state"] == "lapsed" and st["founding"]["no"] == 37
    clock["now"] = server.exp + 5
    st = lic.status()
    assert st["state"] == "expired" and st["founding"]["no"] == 37
    # 服务端判吊销：标记不给
    rec = json.loads(lic._token_path().read_text())
    rec["state"] = "revoked"
    lic._token_path().write_text(json.dumps(rec))
    st = lic.status()
    assert st["state"] == "revoked" and "founding" not in st


@pytest.mark.parametrize(
    "extra",
    [
        {"founding_no": 0, "founding_since": 1_763_000_000},
        {"founding_no": 1000, "founding_since": 1_763_000_000},
        {"founding_no": -3, "founding_since": 1_763_000_000},
        {"founding_no": True, "founding_since": 1_763_000_000},
        {"founding_no": 5.0, "founding_since": 1_763_000_000},
        {"founding_no": "5", "founding_since": 1_763_000_000},
        {"founding_no": 5, "founding_since": 0},
        {"founding_no": 5, "founding_since": -1},
        {"founding_no": 5, "founding_since": True},
        {"founding_no": 5, "founding_since": "1763000000"},
        {"founding_no": 5},
        {"founding_since": 1_763_000_000},
    ],
)
def test_malformed_founding_is_treated_as_absent_and_never_raises(server, extra):
    server.extra = extra
    activated(server)
    st = lic.status()
    assert "founding" not in st
    assert st["state"] == "active" and st["licensed"] is True  # 授权本身不受影响


def test_founding_bounds_are_inclusive(server):
    for no in (1, 999):
        server.extra = {"founding_no": no, "founding_since": 1}
        lic.activate(CODE)
        assert lic.status()["founding"] == {"no": no, "since": 1, "name": ""}


def test_unsigned_founding_cannot_be_forged_by_editing_the_token(server):
    activated(server)

    def tamper(env):
        raw = json.loads(base64.b64decode(env["payload"]))
        raw.update(FOUNDING)
        env["payload"] = _b64(json.dumps(raw, separators=(",", ":")).encode())

    _rewrite(tamper)
    assert "founding" not in lic.status()


# ---------------------------------------------------------------------------
# 铭牌刻字：只存本机，不联网，不进令牌 / 诊断包
# ---------------------------------------------------------------------------
def test_plate_name_roundtrip_is_local_only_and_in_the_license_dir(server):
    server.extra = dict(FOUNDING)
    activated(server)
    assert lic.status()["founding"]["name"] == ""
    n = len(server.calls)
    out = lic.set_plate_name("  林小雨  ")
    assert out["founding"]["name"] == "林小雨" and lic.plate_name() == "林小雨"
    assert len(server.calls) == n  # 零网络
    p = lic._plate_path()
    assert p.parent == lic._token_path().parent and p.parent.name == "license"
    if os.name != "nt":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    # 令牌文件里没有名字
    assert "林小雨" not in lic._token_path().read_text(encoding="utf-8")
    lic.set_plate_name("")
    assert not p.exists() and lic.status()["founding"]["name"] == ""


def test_plate_name_is_cleaned_and_capped(server):
    server.extra = dict(FOUNDING)
    activated(server)
    lic.set_plate_name("a\n\tb\x00c" + "x" * 100)
    got = lic.plate_name()
    assert len(got) == lic.PLATE_NAME_MAX
    assert "\n" not in got and "\x00" not in got and "\t" not in got
    with pytest.raises(lic.LicenseError) as e:
        lic.set_plate_name(123)  # type: ignore[arg-type]
    assert e.value.code == "client_error"


def test_plate_name_survives_release_and_reactivation_and_junk_file_is_empty(server):
    server.extra = dict(FOUNDING)
    activated(server)
    lic.set_plate_name("林小雨")
    lic.release()
    assert lic.plate_name() == "林小雨"
    lic._plate_path().write_text("{not json", encoding="utf-8")
    assert lic.plate_name() == ""
    lic._plate_path().write_text(json.dumps({"name": ["x"]}), encoding="utf-8")
    assert lic.plate_name() == ""


def test_plate_name_is_not_in_status_for_non_founders_and_route_is_gated(
    server, monkeypatch, client
):
    activated(server)
    lic.set_plate_name("路人")
    assert "founding" not in lic.status()
    r = client.post("/api/license/plate-name", json={"name": "林小雨"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    monkeypatch.setenv(lic.ENV_HARD_OFF, "1")
    assert client.post("/api/license/plate-name", json={"name": "x"}).status_code == 404
