"""Sign in with ChatGPT 登录、凭据与刷新（`engine/siwc_login.py` + `engine/siwc_store.py`，ADR 0209）。

**绝不连真实的 OpenAI**：令牌 / 撤销 / JWKS / 型号四个端点全部指到本机 127.0.0.1 随机端口的假服务器；
「浏览器」= 测试自己对 `redirect_uri` 发 GET。判据的主语：

* 授权地址（这次尝试发给 auth.openai.com 的查询串）：首次注册与再次登录各自该带 / 不该带的参数；
* 回调监听器（本进程起的一次性 http.server）：只认本次 state，只收一次；
* 落盘的账号记录（`data_dir()/siwc/accounts/*.json`）：0600、client_id 是已签发的、令牌四样整体替换；
* `public_flow` / `public_status` / 日志：一个令牌字节都没有。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tavotto.engine import siwc_auth, siwc_login, siwc_store
from tavotto.engine.agent_runner import siwc_wire

CID = "oaiapp_testclient1"
SUB = "user-sub-1"
ACCESS = "acc-SECRET-1"
REFRESH = "ref-SECRET-1"


def _b64(obj) -> str:
    raw = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def fake_id_token(**claims) -> str:
    """签名段是占位：流程用例把 `_verify_rs256` 换成恒真（真验签另有用例）。"""
    return f"{_b64({'alg': 'RS256', 'kid': 'k1'})}.{_b64(claims)}.{_b64(b'sig')}"


class FakeOpenAI:
    def __init__(self):
        self.requests: list[dict] = []
        self.token_reply = None  # callable(form) -> (status, body)
        self.revoke_status = 200
        self.models = {
            "models": [
                {"slug": "gpt-a", "display_name": "GPT A", "visibility": "list"},
                {"slug": "hidden", "visibility": "hide"},
            ]
        }
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, status, body):
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                form = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
                fake.requests.append({"path": self.path, "form": form})
                if self.path == "/token":
                    self._reply(*fake.token_reply(form))
                elif self.path == "/revoke":
                    self._reply(fake.revoke_status, None)
                else:
                    self._reply(404, {})

            def do_GET(self):
                fake.requests.append({"path": self.path, "auth": self.headers.get("Authorization")})
                if self.path == "/models":
                    self._reply(200, fake.models)
                elif self.path == "/jwks":
                    self._reply(200, {"keys": [{"kty": "RSA", "kid": "k1", "n": "AQ", "e": "AQ"}]})
                else:
                    self._reply(404, {})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def posts(self, path):
        return [r["form"] for r in self.requests if r["path"] == path and "form" in r]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(siwc_wire.ENV_FLAG, "1")
    f = FakeOpenAI()
    monkeypatch.setattr(siwc_login, "TOKEN_URL", f.base + "/token")
    monkeypatch.setattr(siwc_login, "REVOKE_URL", f.base + "/revoke")
    monkeypatch.setattr(siwc_login, "JWKS_URL", f.base + "/jwks")
    monkeypatch.setattr(siwc_login, "MODELS_URL", f.base + "/models")
    monkeypatch.setattr(siwc_login, "_verify_rs256", lambda *a: True)
    siwc_login.reset_for_tests()
    yield f
    siwc_login.reset_for_tests()
    f.srv.shutdown()


def _token_ok(sub=SUB, scope=siwc_login.SCOPES, access=ACCESS, refresh=REFRESH):
    def reply(form):
        q = getattr(reply, "nonce", "")
        return 200, {
            "access_token": access,
            "refresh_token": refresh,
            "id_token": fake_id_token(
                iss=siwc_login.ISSUER,
                aud=form["client_id"],
                exp=time.time() + 3600,
                nonce=q,
                sub=sub,
                email="a@example.com",
            ),
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": scope,
        }

    return reply


def _browser(flow: dict, fake: FakeOpenAI, **params) -> tuple[int, dict]:
    """模拟浏览器：读授权地址里的 state / nonce，按给定参数回调。→ (回调 HTTP 状态, 授权查询串)。"""
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(flow["authorize_url"]).query))
    if fake.token_reply is not None:
        fake.token_reply.nonce = q["nonce"]
    params.setdefault("state", q["state"])
    url = q["redirect_uri"] + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, q
    except urllib.error.HTTPError as e:
        return e.code, q


def _wait_state(fid: str, timeout=10.0) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = siwc_login.status(fid)
        if st and st["state"] not in (siwc_login.WAITING, siwc_login.EXCHANGING):
            return st
        time.sleep(0.05)
    raise AssertionError(siwc_login.status(fid))


def _sign_in(fake, **kw) -> dict:
    fake.token_reply = _token_ok(**kw)
    flow = siwc_login.start()
    code, _q = _browser(flow, fake, code="the-code", client_id=CID)
    assert code == 200
    return _wait_state(flow["flow_id"])


# --------------------------------------------------------------------------- 首次注册
def test_first_registration_end_to_end(fake, caplog):
    caplog.set_level(logging.DEBUG)
    fake.token_reply = _token_ok()
    flow = siwc_login.start()
    _code, q = _browser(flow, fake, code="the-code", client_id=CID)
    assert q["client_id"] == siwc_store.DYNAMIC_CLIENT_ID
    assert q["agent_name_hint"] == "Tavotto"
    assert q["ext_agent_host_id"].startswith("urn:uuid:")
    assert q["scope"].split() == siwc_login.SCOPES.split()
    assert q["resource"] == "https://api.openai.com/v1"
    assert q["code_challenge_method"] == "S256" and q["code_challenge"]
    assert q["redirect_uri"].startswith("http://127.0.0.1:") and q["redirect_uri"].endswith(
        "/auth/callback"
    )
    assert "id_token_hint" not in q
    st = _wait_state(flow["flow_id"])
    assert st["state"] == siwc_login.SIGNED_IN
    # 换令牌：已签发 client_id、同一个 redirect_uri、同一个 resource、PKCE verifier
    (ex,) = fake.posts("/token")
    assert (
        ex["client_id"] == CID
        and ex["redirect_uri"] == q["redirect_uri"]
        and ex["resource"] == q["resource"]
    )
    assert (
        ex["grant_type"] == "authorization_code"
        and ex["code"] == "the-code"
        and ex["code_verifier"]
    )
    acc = siwc_store.active()
    assert acc.client_id == CID and acc.subject == SUB and acc.access_token == ACCESS
    assert acc.ext_agent_host_id == q["ext_agent_host_id"]
    assert [m["id"] for m in acc.models] == ["gpt-a"]  # 只要 visibility == list
    path = siwc_store.root() / "accounts" / f"{CID}.json"
    if os.name != "nt":
        assert (path.stat().st_mode & 0o777) == 0o600
        assert (siwc_store.root().stat().st_mode & 0o777) == 0o700
    blob = json.dumps(st) + caplog.text + repr(acc) + json.dumps(siwc_store.public_status())
    for secret in (ACCESS, REFRESH, "the-code", ex["code_verifier"]):
        assert secret not in blob


def test_host_id_is_stable_across_sign_ins(fake):
    first = siwc_store.host_id()
    _sign_in(fake)
    assert siwc_store.host_id() == first == siwc_store.active().ext_agent_host_id


def test_callback_with_wrong_state_is_rejected_and_flow_keeps_waiting(fake):
    fake.token_reply = _token_ok()
    flow = siwc_login.start()
    code, _ = _browser(flow, fake, state="forged", code="x", client_id=CID)
    assert code == 400
    assert siwc_login.status(flow["flow_id"])["state"] == siwc_login.WAITING
    assert fake.posts("/token") == []


def test_registration_without_issued_client_id_is_incomplete(fake):
    fake.token_reply = _token_ok()
    flow = siwc_login.start()
    _browser(flow, fake, code="x", client_id=siwc_store.DYNAMIC_CLIENT_ID)
    st = _wait_state(flow["flow_id"])
    assert (st["state"], st["reason"]) == (siwc_login.FAILED, "registration_incomplete")
    assert fake.posts("/token") == [] and siwc_store.active() is None


def test_access_denied_exchanges_nothing(fake):
    fake.token_reply = _token_ok()
    flow = siwc_login.start()
    _browser(flow, fake, error="access_denied")
    assert _wait_state(flow["flow_id"])["state"] == siwc_login.DENIED
    assert fake.posts("/token") == []


def test_missing_plan_scope_is_signed_in_but_plan_disabled(fake):
    st = _sign_in(fake, scope="openid profile email offline_access")
    assert st["state"] == siwc_login.PLAN_DISABLED
    with pytest.raises(siwc_auth.PlanUsageDisabled):
        siwc_login.provider().access_token()
    assert siwc_store.public_status()["plan_usage_enabled"] is False


def test_invalid_id_token_stores_nothing(fake, monkeypatch):
    monkeypatch.setattr(siwc_login, "_verify_rs256", lambda *a: False)
    st = _sign_in(fake)
    assert (st["state"], st["reason"]) == (siwc_login.FAILED, "id_token_invalid")
    assert siwc_store.accounts() == []


def test_busy_preferred_port_falls_back(fake, monkeypatch):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    busy = s.getsockname()[1]
    monkeypatch.setattr(siwc_login, "PREFERRED_PORT", busy)  # 例如 Codex CLI 的登录正占着 1455
    try:
        flow = siwc_login.start()
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(flow["authorize_url"]).query))
        port = int(urllib.parse.urlsplit(q["redirect_uri"]).port)
        assert port != busy and port > 0
        siwc_login.cancel(flow["flow_id"])
    finally:
        s.close()


def test_cancel_closes_the_listener(fake):
    flow = siwc_login.start()
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(flow["authorize_url"]).query))
    assert siwc_login.cancel(flow["flow_id"])["state"] == siwc_login.CANCELLED
    end = time.monotonic() + 5
    while time.monotonic() < end:
        try:
            urllib.request.urlopen(q["redirect_uri"], timeout=1)
        except urllib.error.HTTPError:
            time.sleep(0.05)
            continue
        except OSError:
            return  # 连不上 = 关了
    raise AssertionError("listener still up")


def test_disabled_flag_blocks_everything(fake, monkeypatch):
    monkeypatch.delenv(siwc_wire.ENV_FLAG)
    with pytest.raises(siwc_login.LoginError):
        siwc_login.start()
    assert isinstance(siwc_login.provider(), siwc_auth.DisabledProvider)


# --------------------------------------------------------------------------- 再次登录
def test_reauthorization_reuses_issued_client_and_sends_hints(fake):
    _sign_in(fake)
    siwc_login.logout()
    fake.token_reply = _token_ok(access="acc-2", refresh="ref-2")
    flow = siwc_login.start()
    _c, q = _browser(flow, fake, code="c2")
    assert q["client_id"] == CID
    assert "agent_name_hint" not in q
    assert q["id_token_hint"] and q["login_hint"] == "a@example.com"
    assert _wait_state(flow["flow_id"])["state"] == siwc_login.SIGNED_IN
    assert siwc_store.active().access_token == "acc-2"
    assert len(siwc_store.accounts()) == 1


def test_reauthorization_rejects_other_client_or_other_account(fake):
    _sign_in(fake)
    fake.token_reply = _token_ok()
    flow = siwc_login.start()
    _browser(flow, fake, code="c", client_id="oaiapp_other")
    assert _wait_state(flow["flow_id"])["reason"] == "client_mismatch"
    fake.token_reply = _token_ok(sub="someone-else", access="acc-x")
    flow = siwc_login.start()
    _browser(flow, fake, code="c")
    assert _wait_state(flow["flow_id"])["reason"] == "account_mismatch"
    assert siwc_store.active().access_token == ACCESS  # 原账号的凭据没被换掉


# --------------------------------------------------------------------------- 刷新
def _expire_now():
    acc = siwc_store.active()
    from dataclasses import replace

    siwc_store.save(replace(acc, expires_at_ms=siwc_auth._now_ms() + 60_000))


def test_refresh_rotates_and_replaces_together(fake):
    _sign_in(fake)
    _expire_now()
    fake.token_reply = lambda form: (
        200,
        {"access_token": "acc-new", "refresh_token": "ref-new", "expires_in": 3600},
    )
    assert siwc_login.provider().access_token() == "acc-new"
    form = fake.posts("/token")[-1]
    assert form == {
        "grant_type": "refresh_token",
        "client_id": CID,
        "refresh_token": REFRESH,
        "resource": siwc_login.RESOURCE,
    }
    acc = siwc_store.active()
    assert (acc.access_token, acc.refresh_token) == ("acc-new", "ref-new")
    assert siwc_auth.REQUIRED_SCOPE in acc.scopes  # 刷新没回 scope = 授权不变


def test_terminal_refresh_error_clears_tokens_but_keeps_registration(fake):
    _sign_in(fake)
    _expire_now()
    fake.token_reply = lambda form: (400, {"error": "refresh_token_reused"})
    with pytest.raises(siwc_auth.NeedsLogin):
        siwc_login.provider().access_token()
    acc = siwc_store.load(CID)
    assert acc is not None and acc.access_token == "" and acc.refresh_token == "" and acc.id_token


def test_transient_refresh_error_keeps_tokens(fake):
    _sign_in(fake)
    _expire_now()
    fake.token_reply = lambda form: (503, {})
    with pytest.raises(siwc_auth.TokenUnavailable):
        siwc_login.provider().access_token()
    assert siwc_store.active().refresh_token == REFRESH


def test_refresh_waits_for_another_process_lock(fake, monkeypatch):
    """另一个进程正持有刷新锁（这里用另一个文件描述符模拟：flock / msvcrt 锁对不同描述符互斥）。"""
    _sign_in(fake)
    fd = os.open(siwc_store.root() / "refresh.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        assert siwc_store._try_lock(fd)
        monkeypatch.setattr(siwc_store, "LOCK_TIMEOUT_S", 0.3)
        with pytest.raises(siwc_auth.TokenUnavailable), siwc_store.refresh_lock(timeout_s=0.3):
            pass
    finally:
        siwc_store._unlock(fd)
        os.close(fd)
    with siwc_store.refresh_lock(timeout_s=1):
        pass


# --------------------------------------------------------------------------- 退出
def test_logout_revokes_and_keeps_client_id(fake):
    _sign_in(fake)
    assert siwc_login.logout() == {"revoked": True}
    (rv,) = fake.posts("/revoke")
    assert rv == {"token": REFRESH, "token_type_hint": "refresh_token", "client_id": CID}
    acc = siwc_store.load(CID)
    assert acc.access_token == "" and acc.refresh_token == "" and acc.client_id == CID
    assert siwc_store.public_status()["signed_in"] is False


def test_logout_clears_locally_even_when_revoke_is_not_confirmed(fake):
    _sign_in(fake)
    fake.revoke_status = 503
    assert siwc_login.logout() == {"revoked": False}
    assert siwc_store.load(CID).refresh_token == ""


# --------------------------------------------------------------------------- 存储
def test_store_never_mixes_two_accounts_under_one_client(fake):
    _sign_in(fake)
    from dataclasses import replace

    with pytest.raises(ValueError):
        siwc_store.save(replace(siwc_store.active(), subject="other"))
    with pytest.raises(ValueError):
        siwc_store.save(replace(siwc_store.active(), client_id=siwc_store.DYNAMIC_CLIENT_ID))


# --------------------------------------------------------------------------- 真验签
def test_id_token_signature_and_claims():
    # 硬 import：pycryptodomex 是运行时依赖（pyproject / requirements），缺了就该红，不许静默跳过
    from Cryptodome.Hash import SHA256
    from Cryptodome.PublicKey import RSA
    from Cryptodome.Signature import pkcs1_15

    key = RSA.generate(2048)
    jwk = {
        "kty": "RSA",
        "kid": "k1",
        "n": _b64(key.n.to_bytes((key.n.bit_length() + 7) // 8, "big")),
        "e": _b64(key.e.to_bytes(3, "big")),
    }
    siwc_login.reset_for_tests()
    siwc_login._JWKS["k1"] = jwk
    now = time.time()

    def tok(header=None, **over):
        claims = {
            "iss": siwc_login.ISSUER,
            "aud": CID,
            "exp": now + 600,
            "nonce": "n1",
            "sub": SUB,
            **over,
        }
        si = f"{_b64(header or {'alg': 'RS256', 'kid': 'k1'})}.{_b64(claims)}"
        return f"{si}.{_b64(pkcs1_15.new(key).sign(SHA256.new(si.encode())))}"

    assert siwc_login.verify_id_token(tok(), client_id=CID, nonce="n1", now_s=now)["sub"] == SUB
    assert siwc_login.verify_id_token(tok(aud=["x", CID]), client_id=CID, nonce="n1", now_s=now)
    bad = [
        tok(aud="oaiapp_other"),
        tok(iss="https://evil.example"),
        tok(nonce="n2"),
        tok(exp=now - siwc_login.CLOCK_SKEW_S - 1),
        tok(header={"alg": "none", "kid": "k1"}),
        tok()[:-4] + "AAAA",  # 签名被改
        tok().split(".")[0]
        + "."
        + _b64({"iss": siwc_login.ISSUER, "aud": CID, "exp": now + 600, "nonce": "n1", "sub": "x"})
        + "."
        + tok().split(".")[2],
    ]
    for t in bad:
        with pytest.raises(siwc_login.LoginError):
            siwc_login.verify_id_token(t, client_id=CID, nonce="n1", now_s=now)
    siwc_login.reset_for_tests()


def test_verification_library_missing_fails_closed(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_crypto(name, *a, **k):
        if name.startswith("Cryptodome"):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_crypto)
    with pytest.raises(siwc_login.LoginError) as ei:
        siwc_login._verify_rs256(b"x", b"y", {"n": "AQ", "e": "AQ"})
    assert ei.value.code == "verify_unavailable"


def test_refresh_lock_is_reentrant_in_one_thread(fake):
    """锁内再要令牌（以后的调用者 / 刷新函数）不会自己等自己。"""
    _sign_in(fake)
    t0 = time.monotonic()
    with siwc_store.refresh_lock(timeout_s=0.5), siwc_store.refresh_lock(timeout_s=0.5):
        assert siwc_login.provider().access_token() == ACCESS
    assert time.monotonic() - t0 < 0.5


def test_refresh_lock_times_out_against_another_thread_in_process(fake):
    """进程内另一个线程正持锁（例如正在撤销 / 刷新）：这里按截止时间放弃，不无限等。"""
    _sign_in(fake)
    held, release = threading.Event(), threading.Event()

    def holder():
        with siwc_store.refresh_lock():
            held.set()
            release.wait(5)

    th = threading.Thread(target=holder)
    th.start()
    held.wait(5)
    t0 = time.monotonic()
    try:
        with pytest.raises(siwc_auth.TokenUnavailable), siwc_store.refresh_lock(timeout_s=0.3):
            pass
        assert time.monotonic() - t0 < 2
    finally:
        release.set()
        th.join()


def test_importing_the_flask_app_does_not_load_the_crypto_library():
    """验签库只在登录验签时才加载（延后 import）：Flask 父进程启动不为它付代价。子进程里做，免得别的用例已把它带进 sys.modules。"""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    code = (
        "import sys; import tavotto.app, tavotto.engine.siwc_login; "
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] == 'Cryptodome'); "
        "print(loaded); sys.exit(1 if loaded else 0)"
    )
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=root,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_frozen_build_collects_the_crypto_library():
    """冻结产物：spec 把 Cryptodome 点名收进 hiddenimports（延后 import 静态分析看不到）。"""
    from pathlib import Path

    spec = (Path(__file__).resolve().parent.parent / "packaging" / "tavotto.spec").read_text(
        encoding="utf-8"
    )
    assert 'collect_all("Cryptodome")' in spec and "*CRYPTODOME_MODULES," in spec


def test_large_real_world_model_list_is_not_dropped(fake):
    """真服务的 /v1/models 每个型号带整套元数据，远超 256 KB（2026-11-25 真服务核对时被当成空清单）。"""
    meta = {
        "description": "x" * 4000,
        "supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high")],
    }
    fake.models = {
        "models": [{"slug": f"gpt-{i}", "visibility": "list", **meta} for i in range(120)]
    }
    _sign_in(fake)
    assert len(siwc_store.active().models) == 120


def test_oversized_success_is_not_an_empty_success(fake, monkeypatch):
    monkeypatch.setattr(siwc_login, "MODELS_BODY_LIMIT", 1024)
    fake.models = {"models": [{"slug": "gpt-a", "visibility": "list", "description": "x" * 5000}]}
    _sign_in(fake)
    assert siwc_login.fetch_models(ACCESS) is None  # 拿不到 ≠ 清单是空的


# --------------------------------------------------------------------------- 回调页
@pytest.mark.parametrize(
    ("accept", "ok", "title"),
    [
        ("zh-CN,zh;q=0.9,en;q=0.8", True, "已连接 ChatGPT"),
        ("zh-TW", False, "未能连接 ChatGPT"),
        ("en-US,en;q=0.9", True, "Connected to ChatGPT"),
        ("fr-FR", False, "Couldn’t connect to ChatGPT"),
        ("", True, "Connected to ChatGPT"),
    ],
)
def test_done_page_speaks_one_language(accept, ok, title):
    page = siwc_login.done_page(ok, accept).decode("utf-8")
    assert f"<h1>{title}</h1>" in page
    other = "Connected" if "连接" in title else "连接"
    assert other not in page.split("<body>")[1]  # 不再中英并排


def test_done_page_is_static_and_branded():
    from tavotto.engine import brand_mark

    page = siwc_login.done_page(True, "en").decode("utf-8")
    assert (
        "<script" not in page.lower()
        and "http://" not in page
        and "https://" not in page.replace("http://www.w3.org", "")
    )
    assert all(d in page for _role, d in brand_mark.PATHS)
    assert 'rel="icon"' in page  # 不向回调端口多要一次 /favicon.ico


def test_backend_brand_mark_is_generated_from_the_single_source():
    """`engine/brand_mark.py` 与 scripts/build_brand_assets.py 现在生成的逐字相同（改几何只改生成器）。"""
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "build_brand_assets", root / "scripts" / "build_brand_assets.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (root / "src" / "tavotto" / "engine" / "brand_mark.py").read_text(
        encoding="utf-8"
    ) == mod.mark_py()


def test_model_list_keeps_each_models_reasoning_levels(fake):
    """真服务（2026-11-25）：每个型号带 supported_reasoning_levels 与 default_reasoning_level；只留 SIWC 能发的档。"""
    fake.models = {
        "models": [
            {
                "slug": "gpt-x",
                "visibility": "list",
                "default_reasoning_level": "medium",
                "supported_reasoning_levels": [
                    {"effort": e, "description": "d"}
                    for e in ("minimal", "low", "medium", "high", "xhigh", "max")
                ],
            },
            {"slug": "gpt-y", "visibility": "list"},
        ]
    }
    _sign_in(fake)
    x, y = siwc_store.active().models
    assert x == {
        "id": "gpt-x",
        "efforts": ["low", "medium", "high", "xhigh", "max"],
        "default_effort": "medium",
    }
    assert y == {"id": "gpt-y"}


# --------------------------------------------------------------------------- AI 工具读不到凭据（Codex #2210 P1）
def test_ai_read_tools_cannot_read_siwc_credentials(fake, tmp_path):
    """主语：AI 修复的读文件范围（`ReadScope.check`）、试跑守卫用的同一张硬拦表（`deny_spec`）、CLI 读文件权限规则。
    登录后落盘的账号文件、host.json、整个 siwc 目录都必须被拒；文件名是 oaiapp_*.json，不能靠文件名规则兜。"""
    from tavotto.engine import ai_agents
    from tavotto.engine.fixtools import readscope

    _sign_in(fake)
    acc_file = siwc_store.root() / "accounts" / f"{CID}.json"
    assert acc_file.is_file()
    # 主目录 = 数据目录的上一级：凭据在可读根之内，只能靠硬拦表挡住
    scope = readscope.ReadScope(home=tmp_path, project_root=tmp_path)
    for p in (acc_file, siwc_store.root() / "host.json", siwc_store.root()):
        r = scope.check(str(p))
        assert not r.ok and r.reason == readscope.REASON_DENIED, (p, r)
    assert scope.check(str(tmp_path)).ok  # 对照：别的地方照常可读（判据不是恒拒）

    spec = readscope.deny_spec()
    assert str(siwc_store.root()) in spec["absolute"]
    rules = ai_agents.get_agent("claude").fix_permissions(spec, "tavotto-fix")["deny"]
    root = "//" + str(siwc_store.root()).replace("\\", "/").lstrip("/")
    assert f"Read({root})" in rules and f"Read({root}/**)" in rules
