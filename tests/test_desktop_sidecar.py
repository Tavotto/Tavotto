"""桌面 sidecar（`tavotto --desktop-sidecar`）的护栏。

真 HTTP 打真 server（不是 test_client）：认证、Host/Origin、SSE、握手、
优雅关停走的都是 werkzeug 线程 server 的真实路径。浏览器模式回归用例
确认这些钩子在非桌面模式下完全旁路。
"""
from __future__ import annotations

import http.client
import io
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tavotto import app as appmod
from tavotto import desktop

NONCE = "test-nonce-0123456789abcdef"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
class Sidecar:
    def __init__(self, tmp_path: Path):
        self.handshake = tmp_path / "hs" / "handshake.json"
        self.state = desktop.DesktopState(NONCE)
        self.srv = desktop.SidecarServer(appmod.app, self.state, self.handshake)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.srv.announce_ready()
        self.port = self.srv.port

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def stop(self):
        self.srv.shutdown()
        self.srv.wait_stopped(timeout=10)
        self.thread.join(timeout=5)


@pytest.fixture
def sidecar(tmp_path):
    sc = Sidecar(tmp_path)
    yield sc
    sc.stop()
    # 桌面模式标记必须被清理干净，否则污染其他（浏览器模式）测试
    assert "TAVOTTO_SESSION_STATE" not in appmod.app.config
    assert "TAVOTTO_DESKTOP_MODE" not in appmod.app.config


def http_get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def http_post_json(url: str, body: dict, headers: dict | None = None):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def bootstrap_cookie(sc: Sidecar) -> str:
    status, headers, _ = http_post_json(sc.url(desktop.BOOTSTRAP_PATH),
                                        {"nonce": NONCE})
    assert status == 200
    set_cookie = headers.get("Set-Cookie", "")
    assert desktop.COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie
    return set_cookie.split(";", 1)[0]  # name=value


# ---------------------------------------------------------------------------
# 动态端口
# ---------------------------------------------------------------------------
def test_dynamic_port_never_fixed(tmp_path):
    """端口 0 由 OS 分配：与任何已占用端口天然不冲突（5089 被占也能启动）。"""
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocked_port = blocker.getsockname()[1]
    try:
        sc = Sidecar(tmp_path)
        try:
            assert 1024 < sc.port < 65536
            assert sc.port != blocked_port
        finally:
            sc.stop()
    finally:
        blocker.close()


def test_two_sidecars_get_distinct_ports(tmp_path):
    a = Sidecar(tmp_path / "a")
    b = Sidecar(tmp_path / "b")
    try:
        assert a.port != b.port
    finally:
        b.stop()
        a.stop()


# ---------------------------------------------------------------------------
# 握手文件
# ---------------------------------------------------------------------------
def test_handshake_ready_no_secret(sidecar):
    data = json.loads(sidecar.handshake.read_text(encoding="utf-8"))
    assert data == {"ready": True, "pid": os.getpid(), "port": sidecar.port}
    assert NONCE not in sidecar.handshake.read_text(encoding="utf-8")


def test_handshake_error_and_cleanup(tmp_path):
    p = tmp_path / "hs.json"
    desktop.write_handshake(p, ready=False, error="boom")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["ready"] is False and data["error"] == "boom"
    sc = Sidecar(tmp_path)
    hs = sc.handshake
    assert hs.is_file()
    sc.stop()
    assert not hs.exists()  # 退出时清理


# ---------------------------------------------------------------------------
# bootstrap 认证与重放
# ---------------------------------------------------------------------------
def test_bootstrap_flow_and_replay(sidecar):
    # 未认证访问敏感 API → 401（连 409 no_project 都不该看到）
    status, _, body = http_get(sidecar.url("/api/panels"))
    assert status == 401
    assert json.loads(body)["code"] == "session_auth_required"

    # 错误 nonce → 403，且不作废真 nonce
    status, _, body = http_post_json(sidecar.url(desktop.BOOTSTRAP_PATH),
                                     {"nonce": "wrong"})
    assert status == 403 and json.loads(body)["code"] == "bad_nonce"

    cookie = bootstrap_cookie(sidecar)

    # 重放同一 nonce → 403（一次性）
    status, _, _ = http_post_json(sidecar.url(desktop.BOOTSTRAP_PATH),
                                  {"nonce": NONCE})
    assert status == 403

    # 有 cookie → 放行到业务层：没开项目时 409 no_project；全量测试里其他用例
    # 可能已把默认项目打开（进程级状态），那时是 200——两者都证明穿过了认证
    status, _, body = http_get(sidecar.url("/api/panels"),
                               headers={"Cookie": cookie})
    assert status in (200, 409)
    if status == 409:
        assert json.loads(body)["code"] == "no_project"

    # 伪造 cookie → 401
    status, _, _ = http_get(sidecar.url("/api/panels"),
                            headers={"Cookie": f"{desktop.COOKIE_NAME}=forged"})
    assert status == 401


def test_sse_and_exports_require_auth(sidecar):
    status, _, _ = http_get(sidecar.url("/api/events"))
    assert status == 401
    status, _, _ = http_get(sidecar.url("/exports/whatever.pdf"))
    assert status == 401
    status, _, _ = http_get(sidecar.url("/api/render?id=x&w=400"))
    assert status == 401


def test_public_paths_no_auth(sidecar):
    """首屏 HTML / 静态资源不需要会话（页面要先加载才能跑 bootstrap）。"""
    status, _, _ = http_get(sidecar.url("/"))
    assert status in (200, 503)  # 测试环境可能没有前端构建产物 → 503 提示页
    status, _, _ = http_get(sidecar.url("/assets/nonexistent.js"))
    assert status != 401


# ---------------------------------------------------------------------------
# Host / Origin
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


def test_host_check(sidecar):
    status, body = _raw_request(sidecar.port, "/api/version", host="evil.example")
    assert status == 403 and body["code"] == "bad_host"
    # localhost 拼法也拒：只认 127.0.0.1:<port> 一种写法，堵 DNS rebinding
    status, body = _raw_request(sidecar.port, "/api/version",
                                host=f"localhost:{sidecar.port}")
    assert status == 403


def test_origin_check(sidecar):
    cookie = bootstrap_cookie(sidecar)
    good = f"127.0.0.1:{sidecar.port}"
    status, body = _raw_request(sidecar.port, "/api/version", host=good,
                                origin="http://evil.example", cookie=cookie)
    assert status == 403 and body["code"] == "bad_origin"
    status, _ = _raw_request(sidecar.port, "/api/version", host=good,
                             origin=f"http://{good}", cookie=cookie)
    assert status == 200


# ---------------------------------------------------------------------------
# updater 在桌面模式下停用
# ---------------------------------------------------------------------------
def test_updater_disabled_in_desktop(sidecar):
    cookie = bootstrap_cookie(sidecar)
    status, _, body = http_get(sidecar.url("/api/update/check"),
                               headers={"Cookie": cookie})
    assert status == 200
    data = json.loads(body)
    assert data["desktop"] is True and data["update_available"] is False

    status, _, body = http_post_json(sidecar.url("/api/update/apply"), {},
                                     headers={"Cookie": cookie})
    assert status == 409
    assert json.loads(body)["code"] == "desktop_updater_disabled"


# ---------------------------------------------------------------------------
# 优雅关停与父进程监视
# ---------------------------------------------------------------------------
def test_graceful_shutdown_frees_port(tmp_path):
    sc = Sidecar(tmp_path)
    port = sc.port
    sc.stop()
    with pytest.raises((ConnectionRefusedError, urllib.error.URLError, OSError)):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
    # 幂等：重复 shutdown 不炸
    sc.srv.shutdown()


def test_parent_stdin_eof_triggers_shutdown(tmp_path):
    """父进程消失（stdin EOF）→ sidecar 自行优雅退出，不留孤儿。"""
    sc = Sidecar(tmp_path)
    r_fd, w_fd = os.pipe()
    r = os.fdopen(r_fd, "rb")
    try:
        desktop.watch_stdin_eof(r, sc.srv.shutdown)
        os.close(w_fd)  # 模拟 Tauri 退出：写端关闭 → EOF
        assert sc.srv.wait_stopped(timeout=10), "stdin EOF 后 server 未退出"
    finally:
        r.close()
        sc.thread.join(timeout=5)
    assert not sc.handshake.exists()


# ---------------------------------------------------------------------------
# 启动凭据读取
# ---------------------------------------------------------------------------
def test_read_credentials_env_takes_priority_and_is_scrubbed():
    env = {"TAVOTTO_DESKTOP_NONCE": "env-nonce",
           "TAVOTTO_DESKTOP_PARENT_PID": "4242"}
    nonce, pid, _ = desktop.read_launch_credentials(stdin=io.StringIO(""),
                                                    environ=env)
    assert nonce == "env-nonce" and pid == 4242
    assert "TAVOTTO_DESKTOP_NONCE" not in env  # 用后即焚，不让子进程继承


def test_read_credentials_from_stdin_line():
    stream = io.StringIO(json.dumps({"nonce": "stdin-nonce",
                                     "parent_pid": 77}) + "\n")
    nonce, pid, returned = desktop.read_launch_credentials(stdin=stream,
                                                           environ={})
    assert nonce == "stdin-nonce" and pid == 77
    assert returned is stream  # 首行之后的流留给父进程监视


def test_read_credentials_tty_never_blocks():
    class Tty(io.StringIO):
        def isatty(self):
            return True

        def readline(self, *a):  # pragma: no cover - 若被调用即失败
            raise AssertionError("tty stdin 不应被读取（会无限阻塞）")

    nonce, pid, _ = desktop.read_launch_credentials(stdin=Tty(), environ={})
    assert nonce is None and pid is None


def test_run_refuses_without_nonce(tmp_path, monkeypatch):
    """没有启动凭据坚决不起无认证的桌面后端：握手报错 + 非零退出码。"""
    hs = tmp_path / "hs.json"
    monkeypatch.setenv("TAVOTTO_DESKTOP_HANDSHAKE", str(hs))
    monkeypatch.delenv("TAVOTTO_DESKTOP_NONCE", raising=False)

    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(desktop.sys, "stdin", Tty())
    assert desktop.run(appmod.app) == 2
    data = json.loads(hs.read_text(encoding="utf-8"))
    assert data["ready"] is False and "凭据" in data["error"]
    assert "TAVOTTO_SESSION_STATE" not in appmod.app.config


# ---------------------------------------------------------------------------
# Windows：引擎子进程不得弹控制台（frozen 窗口化父进程下的老坑）
# ---------------------------------------------------------------------------
def test_engine_subprocess_calls_never_pop_console_windows():
    """所有引擎子进程 spawn 必须显式带 creationflags=NO_WINDOW **和 stdin=**。

    frozen 窗口化父进程（Tauri sidecar / 独立应用）下的两个真坑
    （Windows Server 2025 实测，症状都是桌面版「渲染环境不可用」而同一
    解释器在终端里探测秒过）：

    1. 没有控制台的父进程起 console 子进程会触发新建控制台：交互桌面上
       闪黑窗，SSH / 服务这类无交互桌面会话里更糟 → creationflags=NO_WINDOW。
    2. 不显式给 stdin 时子进程继承 sidecar 的 stdin——那是「父进程死亡信号」
       管道，绝不能外传；且实测继承它会让子解释器启动直接挂死（30s 超时）。
       探测/安装/AI CLI 一律 stdin=DEVNULL，渲染 worker 是 stdin=PIPE（协议）。
    """
    import re
    root = Path(__file__).resolve().parent.parent / "src" / "tavotto"
    files = [root / "engine" / n for n in
             ("pool.py", "bootstrap.py", "ai_bridge.py")] + [root / "app.py"]
    missing = []
    for path in files:
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"(?:subprocess|sp)\.(?:run|Popen)\(", src):
            window = src[m.start():m.start() + 700]
            for required in ("creationflags", "stdin="):
                if required not in window:
                    line = src[:m.start()].count("\n") + 1
                    missing.append(f"{path.name}:{line} 缺 {required}")
    assert not missing, f"引擎子进程调用不合规: {missing}"


def test_no_window_flag_value():
    import subprocess as sp

    from tavotto.engine import runtime
    if os.name == "nt":
        assert runtime.CREATE_NO_WINDOW == sp.CREATE_NO_WINDOW
    else:
        assert runtime.CREATE_NO_WINDOW == 0  # 非 Windows 上必须是无操作


# ---------------------------------------------------------------------------
# 浏览器 / CLI 模式回归：桌面钩子必须完全旁路
# ---------------------------------------------------------------------------
def test_browser_mode_untouched():
    client = appmod.app.test_client()
    # 无 cookie、无 Host 白名单（test_client 的 Host 是 localhost）也照常服务
    resp = client.get("/api/version")
    assert resp.status_code == 200
    assert "build" in resp.get_json()
    # bootstrap 端点在非桌面模式下不存在（404），不暴露任何桌面语义
    resp = client.post(desktop.BOOTSTRAP_PATH, json={"nonce": "x"})
    assert resp.status_code == 404
