"""浏览器模式会话认证（P0 修复，ADR 0008）的护栏。

真 HTTP 打真 server：浏览器模式与桌面模式共用 security.py 的同一道边界，
这里验证浏览器模式那份参数化——一次性 nonce → 带 Max-Age 的 HttpOnly
cookie、本机凭据文件 + `X-Tavotto-Auth` 头、`/api/session/relaunch` 的
安全实例复用交接，以及 DNS rebinding / Host / Origin / 重放 / 无 cookie /
伪造 cookie 的全套拒绝路径。
"""
from __future__ import annotations

import http.client
import json
import os
import stat
import threading
import urllib.error
import urllib.request

import pytest
from werkzeug.serving import make_server

from tavotto import app as appmod, security
from tavotto.engine import session_client

NONCE_BYTES = 32


class BrowserApp:
    """浏览器模式的最小复刻：make_server + security.new_browser_state。"""

    def __init__(self):
        self._srv = make_server("127.0.0.1", 0, appmod.app, threaded=True)
        self.port = self._srv.server_port
        self.state, self.nonce = security.new_browser_state(self.port)
        appmod.app.config[security.STATE_KEY] = self.state
        self.thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self.thread.start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def stop(self):
        self._srv.shutdown()
        self.thread.join(timeout=5)
        appmod.app.config.pop(security.STATE_KEY, None)
        session_client.remove_secret(self.port)


@pytest.fixture
def served():
    srv = BrowserApp()
    yield srv
    srv.stop()
    assert security.STATE_KEY not in appmod.app.config


def http_get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def http_post_json(url: str, body: dict, headers: dict | None = None):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def bootstrap_cookie(srv: BrowserApp, nonce: str | None = None) -> str:
    status, headers, _ = http_post_json(srv.url(security.BOOTSTRAP_PATH),
                                        {"nonce": nonce or srv.nonce})
    assert status == 200
    set_cookie = headers.get("Set-Cookie", "")
    assert security.COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie
    return set_cookie.split(";", 1)[0]


# ---------------------------------------------------------------------------
# 默认拒绝：高权限 API 一个都不放
# ---------------------------------------------------------------------------
def test_api_default_deny_without_session(served):
    for path in ("/api/panels", "/api/projects/recent", "/api/events",
                 "/api/ai/capabilities", "/api/update/check",
                 "/api/render?id=x&w=400", "/exports/whatever.pdf",
                 "/api/browse?path=/", "/api/autosave/doc1"):
        status, _, body = http_get(served.url(path))
        assert status == 401, f"{path} 未认证也放行了"
        assert json.loads(body)["code"] == "session_auth_required"


def test_write_endpoints_default_deny(served):
    for path in ("/api/projects/open", "/api/ai/run", "/api/update/apply",
                 "/api/engine/update_source", "/api/export"):
        status, _, body = http_post_json(served.url(path), {})
        assert status == 401, f"{path} 未认证也放行了"


def test_public_surface_is_minimal(served):
    # 首屏与静态资源要能加载（页面得先起来才能跑 bootstrap）
    status, _, _ = http_get(served.url("/"))
    assert status in (200, 503)
    # /api/version 是实例探测的判据，只回版本信息
    status, _, body = http_get(served.url("/api/version"))
    assert status == 200 and "build" in json.loads(body)


# ---------------------------------------------------------------------------
# bootstrap：一次性 nonce、重放、cookie
# ---------------------------------------------------------------------------
def test_bootstrap_flow_replay_and_forged_cookie(served):
    # 错误 nonce → 403，且不作废真 nonce
    status, _, body = http_post_json(served.url(security.BOOTSTRAP_PATH),
                                     {"nonce": "wrong"})
    assert status == 403 and json.loads(body)["code"] == "bad_nonce"

    cookie = bootstrap_cookie(served)
    # 浏览器模式的 cookie 带 Max-Age（服务器常驻、浏览器会整个重开）
    # 重放同一 nonce → 403（一次性）
    status, _, _ = http_post_json(served.url(security.BOOTSTRAP_PATH),
                                  {"nonce": served.nonce})
    assert status == 403

    status, _, _ = http_get(served.url("/api/panels"), headers={"Cookie": cookie})
    assert status in (200, 409)  # 穿过认证到业务层（可能没开项目 → 409）

    status, _, _ = http_get(served.url("/api/panels"),
                            headers={"Cookie": f"{security.COOKIE_NAME}=forged"})
    assert status == 401


def test_browser_cookie_has_max_age(served):
    _, headers, _ = http_post_json(served.url(security.BOOTSTRAP_PATH),
                                   {"nonce": served.nonce})
    assert "Max-Age" in headers.get("Set-Cookie", "")


def test_legacy_bootstrap_alias_works(served):
    status, headers, _ = http_post_json(served.url(security.LEGACY_BOOTSTRAP_PATH),
                                        {"nonce": served.nonce})
    assert status == 200 and security.COOKIE_NAME in headers.get("Set-Cookie", "")


# ---------------------------------------------------------------------------
# Host / Origin（DNS rebinding 与本机恶意页面）
# ---------------------------------------------------------------------------
def _raw_request(port: int, path: str, host: str, origin: str | None = None,
                 cookie: str | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest("GET", path, skip_host=True)
        conn.putheader("Host", host)
        if origin:
            conn.putheader("Origin", origin)
        if cookie:
            conn.putheader("Cookie", cookie)
        conn.endheaders()
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")
    finally:
        conn.close()


def test_dns_rebinding_host_rejected(served):
    """DNS rebinding：攻击者域名解析到 127.0.0.1，Host 是攻击者域名 → 拒。"""
    for host in ("evil.example", f"evil.example:{served.port}",
                 f"localhost:{served.port}", f"[::1]:{served.port}"):
        status, body = _raw_request(served.port, "/api/version", host=host)
        assert status == 403 and body["code"] == "bad_host", host


def test_cross_origin_rejected_even_with_cookie(served):
    cookie = bootstrap_cookie(served)
    good_host = f"127.0.0.1:{served.port}"
    status, body = _raw_request(served.port, "/api/version", host=good_host,
                                origin="http://evil.example", cookie=cookie)
    assert status == 403 and body["code"] == "bad_origin"
    status, _ = _raw_request(served.port, "/api/version", host=good_host,
                             origin=f"http://{good_host}", cookie=cookie)
    assert status == 200


# ---------------------------------------------------------------------------
# 本机凭据：X-Tavotto-Auth 头与 relaunch 交接
# ---------------------------------------------------------------------------
def test_local_secret_header_authenticates(served):
    secret = session_client.read_secret(served.port)
    assert secret
    status, _, _ = http_get(served.url("/api/panels"),
                            headers={session_client.AUTH_HEADER: secret})
    assert status in (200, 409)
    status, _, _ = http_get(served.url("/api/panels"),
                            headers={session_client.AUTH_HEADER: "forged"})
    assert status == 401


def test_secret_file_permissions_and_cleanup():
    srv = BrowserApp()
    path = session_client.session_file_path(srv.port)
    try:
        assert os.path.isfile(path)
        if os.name != "nt":
            assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
            assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
        data = json.loads(open(path, encoding="utf-8").read())
        assert data["port"] == srv.port and data["pid"] == os.getpid()
    finally:
        srv.stop()
    assert not os.path.exists(path)  # 退出清理


def test_relaunch_handoff_issues_fresh_one_time_nonce(served):
    # 第二次 `tavotto` 启动的路径：凭据文件 → relaunch → 新 nonce → cookie
    nonce = session_client.relaunch_nonce(served.port)
    assert nonce and nonce != served.nonce
    cookie = bootstrap_cookie(served, nonce)
    status, _, _ = http_get(served.url("/api/panels"), headers={"Cookie": cookie})
    assert status in (200, 409)
    # relaunch 的 nonce 同样一次性
    status, _, _ = http_post_json(served.url(security.BOOTSTRAP_PATH),
                                  {"nonce": nonce})
    assert status == 403


def test_relaunch_rejects_wrong_secret(served):
    status, _, body = http_post_json(served.url(session_client.RELAUNCH_PATH),
                                     {"secret": "wrong"})
    assert status == 403 and json.loads(body)["code"] == "bad_secret"


def test_relaunch_nonce_does_not_kill_existing_session(served):
    """实例复用绝不顶掉已在用的浏览器会话：两个 cookie 并存。"""
    first = bootstrap_cookie(served)
    nonce = session_client.relaunch_nonce(served.port)
    second = bootstrap_cookie(served, nonce)
    for cookie in (first, second):
        status, _, _ = http_get(served.url("/api/panels"),
                                headers={"Cookie": cookie})
        assert status in (200, 409)


# ---------------------------------------------------------------------------
# ping：前端「有没有会话」的探针
# ---------------------------------------------------------------------------
def test_session_ping(served):
    status, _, _ = http_get(served.url(security.PING_PATH))
    assert status == 401
    cookie = bootstrap_cookie(served)
    status, _, body = http_get(served.url(security.PING_PATH),
                               headers={"Cookie": cookie})
    assert status == 200 and json.loads(body)["ok"] is True


# ---------------------------------------------------------------------------
# 旁路档（test_client / --insecure-no-auth）保持原样
# ---------------------------------------------------------------------------
def test_no_state_means_no_enforcement_and_no_session_endpoints():
    client = appmod.app.test_client()
    assert client.get("/api/version").status_code == 200
    assert client.post(security.BOOTSTRAP_PATH, json={"nonce": "x"}).status_code == 404
    assert client.post(session_client.RELAUNCH_PATH,
                       json={"secret": "x"}).status_code == 404
    assert client.get(security.PING_PATH).get_json()["ok"] is True


# ---------------------------------------------------------------------------
# 客户端助手的失败路径
# ---------------------------------------------------------------------------
def test_session_client_tolerates_missing_or_garbage_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path))
    assert session_client.read_secret(59999) is None
    assert session_client.auth_headers(59999) == {}
    assert session_client.relaunch_nonce(59999) is None
    p = tmp_path / "session"
    p.mkdir()
    (p / "port-59999.json").write_text("not json", encoding="utf-8")
    assert session_client.read_secret(59999) is None
