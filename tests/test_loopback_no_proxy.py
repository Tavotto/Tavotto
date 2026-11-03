"""带凭据的本机回环请求绝不经过代理（#1132）。

`relaunch_nonce`（完整会话 secret）、`handoff._http_json` / `_http_json_status`（`X-Tavotto-Auth`）原来走
`urllib.request.urlopen`，后者按 `HTTP_PROXY` / `ALL_PROXY` / 系统设置走代理，凭据会发给代理。
判据的主语：环境里配了代理（NO_PROXY 清空）、一个假代理在监听、一个真回环服务当实例——
实例收到了带凭据的请求，假代理 0 个连接。换回 `urlopen` 这几条就红。
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tavotto.engine import handoff, session_client

SECRET = "loopback-secret-1132"


class _Instance:
    def __init__(self):
        seen = self.seen = []

        class H(BaseHTTPRequestHandler):
            def _reply(self, code=200, body=None):
                raw = json.dumps(
                    body if body is not None else {"nonce": "n-1", "build": "x"}
                ).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                seen.append(
                    (self.command, self.path, self.headers.get(session_client.AUTH_HEADER), b"")
                )
                if self.path.startswith("/big"):
                    return self._reply(200, {"items": ["x" * 1000] * 2000})  # > 1 MiB 的合法 JSON
                self._reply(
                    409 if self.path.startswith("/conflict") else 200,
                    {"code": "probe_in_progress"} if self.path.startswith("/conflict") else None,
                )

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                seen.append(
                    (self.command, self.path, self.headers.get(session_client.AUTH_HEADER), body)
                )
                self._reply()

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path))
    inst = _Instance()
    session_client.publish_secret(inst.port, SECRET)
    yield inst
    inst.stop()


@pytest.fixture
def fake_proxy(monkeypatch):
    # 模块级 `urlopen` 第一次调用就把当时的代理环境缓存进全局 opener：清掉，别让用例顺序决定红绿
    monkeypatch.setattr(urllib.request, "_opener", None)
    proxy = socket.socket()
    proxy.bind(("127.0.0.1", 0))
    proxy.listen(8)
    proxy.settimeout(0.3)
    url = f"http://127.0.0.1:{proxy.getsockname()[1]}"
    for name in (
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
        "HTTPS_PROXY",
        "https_proxy",
    ):
        monkeypatch.setenv(name, url)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    yield proxy
    proxy.close()


def _assert_proxy_untouched(proxy):
    with pytest.raises(socket.timeout):
        proxy.accept()


def test_relaunch_nonce_never_goes_through_a_proxy(instance, fake_proxy):
    assert session_client.relaunch_nonce(instance.port, timeout=3) == "n-1"
    method, path, _auth, body = instance.seen[0]
    assert (method, path) == ("POST", session_client.RELAUNCH_PATH)
    assert json.loads(body) == {"secret": SECRET}  # 完整 secret 直达实例
    _assert_proxy_untouched(fake_proxy)


def test_handoff_http_json_never_goes_through_a_proxy(instance, fake_proxy):
    out = handoff._http_json(f"http://127.0.0.1:{instance.port}/api/version?x=1", timeout=3)
    assert out == {"nonce": "n-1", "build": "x"}
    assert instance.seen[0][1:3] == ("/api/version?x=1", SECRET)
    out = handoff._http_json(
        f"http://127.0.0.1:{instance.port}/api/projects/open", {"a": 1}, timeout=3
    )
    assert out is not None
    assert instance.seen[1][0] == "POST" and instance.seen[1][2] == SECRET
    assert json.loads(instance.seen[1][3]) == {"a": 1}
    _assert_proxy_untouched(fake_proxy)


def test_handoff_http_json_status_never_goes_through_a_proxy(instance, fake_proxy):
    status, body = handoff._http_json_status(
        f"http://127.0.0.1:{instance.port}/conflict", timeout=3
    )
    assert (status, body) == (409, {"code": "probe_in_progress"})  # 错误响应体照样交出来
    assert instance.seen[0][2] == SECRET
    status, body = handoff._http_json_status(
        f"http://127.0.0.1:{instance.port}/api/probe", {"s": "x"}, timeout=3
    )
    assert status == 200 and instance.seen[1][2] == SECRET
    _assert_proxy_untouched(fake_proxy)


def test_unreachable_instance_keeps_the_none_semantics(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path))
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # 此刻没人监听
    assert handoff._http_json(f"http://127.0.0.1:{port}/api/version", timeout=0.5) is None
    assert handoff._http_json_status(f"http://127.0.0.1:{port}/x", timeout=0.5) == (None, None)
    session_client.publish_secret(port, SECRET)
    assert session_client.relaunch_nonce(port, timeout=0.5) is None


def test_large_valid_json_responses_are_read_in_full(instance):
    """refresh / readiness / probe 的响应随项目规模增长：超过 1 MiB 的合法 JSON 也要完整解出来（原 urlopen 完整读取）。"""
    url = f"http://127.0.0.1:{instance.port}/big"
    status, body = handoff._http_json_status(url, timeout=5)
    assert status == 200 and len(body["items"]) == 2000
    assert len(handoff._http_json(url, timeout=5)["items"]) == 2000


def test_relaunch_nonce_response_is_capped(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(session_client, "RELAUNCH_MAX_RESPONSE_BYTES", 8)
    inst = _Instance()
    try:
        session_client.publish_secret(inst.port, SECRET)
        assert session_client.relaunch_nonce(inst.port, timeout=3) is None  # 截断后读不懂 = None
    finally:
        inst.stop()
