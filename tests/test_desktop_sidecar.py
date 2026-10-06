"""桌面 sidecar（`tavotto --desktop-sidecar`）的护栏。

真 HTTP 打真 server（不是 test_client）：认证、Host/Origin、SSE、握手、
优雅关停走的都是 werkzeug 线程 server 的真实路径。浏览器模式回归用例
确认这些钩子在非桌面模式下完全旁路。
"""

from __future__ import annotations

import http.client
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tavotto import app as appmod, desktop

NONCE = "test-nonce-0123456789abcdef"


@pytest.mark.skipif(
    any(importlib.util.find_spec(name) is None for name in ("pypdfium2", "pikepdf")),
    reason="真实 render child 依赖未装（not_run）",
)
@pytest.mark.parametrize("cleanup_error", [False, True])
def test_desktop_main_reaps_its_real_renderer_before_returning(tmp_path, cleanup_error):
    """量 main 返回那一刻的 Popen.returncode，不让测试自己的 poll/wait 冒充应用回收。

    独立 sidecar 进程真走 stdin EOF、server cleanup；finally 的备用 close 只收本用例的 child，
    保证反证（删掉最终收尾）判红时也不向测试机泄漏进程。这里不推断历史 macOS PID 的身份。
    """
    script = r"""
import io, json, os, sys
assert (sys.stdout.encoding, sys.stderr.encoding) == ("cp1252", "cp1252")
from pathlib import Path
from tavotto import app as a, desktop
from tavotto.rendercore import renderhost as rh

root = Path(sys.argv[1])
cleanup_error = sys.argv[2] == "True"
handshake = root / "handshake.json"
os.environ["TAVOTTO_DESKTOP_HANDSHAKE"] = str(handshake)
a.setup_logging = lambda: None
a.engine_locate.refresh_manifest = lambda: None
a.engine_config.last_project = lambda: None
a.prune_render_cache = lambda: None
a.engine_pool.prune_engine_cache = lambda: None
a.engine_runtimeasset.prune_cache = lambda: None
a.engine_ai_history.mark_interrupted_running = lambda: 0
a.engine_ai_history.purge = lambda **kw: None
a.engine_telemetry.note_app_started = lambda *args: None
host = rh.shared()
def warm():
    host.ping()
    return "rendercore"
a.pdfbackend.warm = warm
if cleanup_error:
    def fail_cleanup(*args, **kwargs):
        raise RuntimeError("injected worker cleanup failure")
    desktop.engine_pool.shutdown_all = fail_cleanup
sys.argv = ["tavotto", "--desktop-sidecar"]
sys.stdin = io.StringIO(json.dumps({"nonce": "test-final-exit-nonce"}) + "\n")
try:
    # 保存真正的 Popen 引用：最终收尾会把 host._proc 清空。
    warm()
    proc = host._proc
    try:
        a.main()
    except SystemExit as exc:
        assert (sys.stdout.encoding, sys.stderr.encoding) == ("utf-8", "utf-8")
        result = {"exit": exc.code, "renderer_returncode": proc.returncode,
                  "handshake_removed": not handshake.exists()}
        (root / "result.json").write_text(json.dumps(result), encoding="utf-8")
finally:
    host.close()
"""
    src_root = Path(desktop.__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), str(cleanup_error)],
        env={
            **os.environ,
            "PYTHONPATH": str(src_root),
            "TAVOTTO_DATA_DIR": str(tmp_path / "data"),
            "TAVOTTO_CONFIG_DIR": str(tmp_path / "config"),
            "TAVOTTO_NO_TELEMETRY": "1",
            "PYTHONIOENCODING": "cp1252",  # 真走 CLI 重配，覆盖 Windows 重定向管道的初始编码。
        },
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    if cleanup_error:
        assert "injected worker cleanup failure" in out.stderr
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result == {"exit": 0, "renderer_returncode": 0, "handshake_removed": True}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
class Sidecar:
    def __init__(self, tmp_path: Path, **server_kwargs):
        self.handshake = tmp_path / "hs" / "handshake.json"
        self.state = desktop.DesktopState(NONCE)
        self.srv = desktop.SidecarServer(appmod.app, self.state, self.handshake, **server_kwargs)
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
        url, data=data, headers={"Content-Type": "application/json", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def bootstrap_cookie(sc: Sidecar) -> str:
    status, headers, _ = http_post_json(sc.url(desktop.BOOTSTRAP_PATH), {"nonce": NONCE})
    assert status == 200
    set_cookie = headers.get("Set-Cookie", "")
    assert desktop.COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie
    return set_cookie.split(";", 1)[0]  # name=value


# ---------------------------------------------------------------------------
# 端口：优先壳建议的端口，占不到退回系统分配（issue #715 PR-A，ADR 0108）
# ---------------------------------------------------------------------------
GOLDEN_PREFERRED_PORT = Path(__file__).parent / "golden" / "desktop_preferred_port.json"


def _free_port() -> int:
    """此刻空着的一个端口（拿来当建议端口；用例之间不共享）。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _listening_blocker(port: int = 0) -> socket.socket:
    """在 `port` 上真 listen 的占位 socket——「端口被别的程序占着」的最强形态。"""
    s = socket.socket()
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


def test_without_a_preferred_port_the_os_assigns_one(tmp_path):
    """没有建议端口（旧壳 / 调试启动）：端口 0 由 OS 分配，与已占用端口天然不冲突。"""
    blocker = _listening_blocker()
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


def test_free_preferred_port_is_used(tmp_path):
    port = _free_port()
    sc = Sidecar(tmp_path, preferred_port=port)
    try:
        assert sc.port == port
        assert sc.state.port == port  # Host / Origin 校验钉的就是它
        hs = json.loads(sc.handshake.read_text(encoding="utf-8"))
        assert hs["port"] == port
    finally:
        sc.stop()


def test_occupied_preferred_port_falls_back_and_host_check_follows(tmp_path):
    """建议端口被占：本次退回系统分配；Host 校验钉**实际**端口，建议的那个一律 403。"""
    blocker = _listening_blocker()
    blocked = blocker.getsockname()[1]
    try:
        sc = Sidecar(tmp_path, preferred_port=blocked, retry_seconds=0.3)
        try:
            assert sc.port != blocked
            hs = json.loads(sc.handshake.read_text(encoding="utf-8"))
            assert hs["port"] == sc.port  # 壳按握手里的实际端口导航、记落空
            status, _ = _raw_request(sc.port, "/api/version", host=f"127.0.0.1:{sc.port}")
            assert status == 200
            status, body = _raw_request(sc.port, "/api/version", host=f"127.0.0.1:{blocked}")
            assert status == 403 and body["code"] == "bad_host"
        finally:
            sc.stop()
    finally:
        blocker.close()


def test_a_running_sidecar_keeps_its_port_from_a_second_one(tmp_path):
    """两个 sidecar 建议同一个端口：后来的那个退回系统分配，绝不与前一个共用端口。

    Windows 上这条靠 `SO_EXCLUSIVEADDRUSE`（`localserver.bind_options`）：带 `SO_REUSEADDR`
    的话两个都 bind 得上，请求随机落到其中一个。
    """
    port = _free_port()
    a = Sidecar(tmp_path / "a", preferred_port=port)
    try:
        assert a.port == port
        b = Sidecar(tmp_path / "b", preferred_port=port, retry_seconds=0.3)
        try:
            assert b.port != port
        finally:
            b.stop()
    finally:
        a.stop()


def test_preferred_port_freed_within_the_retry_window_is_still_taken(tmp_path):
    """应用内更新后的重启：旧 sidecar 要等 stdin EOF 才放端口——在重试窗口内放出来就还用它。"""
    blocker = _listening_blocker()
    port = blocker.getsockname()[1]
    releaser = threading.Timer(0.6, blocker.close)
    releaser.start()
    try:
        started = time.monotonic()
        sc = Sidecar(tmp_path, preferred_port=port, retry_seconds=5)
        try:
            assert sc.port == port
            assert time.monotonic() - started >= 0.5  # 真的等过：不是一上来就占到的
        finally:
            sc.stop()
    finally:
        releaser.cancel()
        blocker.close()


def test_a_stopped_sidecar_releases_its_port_at_once(tmp_path):
    """停下来的 sidecar 在关 worker 之前就放掉监听端口（关 worker 要等好几秒）：紧接着起来的
    下一个 sidecar（应用内更新后的重启）不必等重试窗口，也不会因此换端口。"""
    port = _free_port()
    a = Sidecar(tmp_path / "a", preferred_port=port)
    assert a.port == port
    a.stop()
    b = Sidecar(tmp_path / "b", preferred_port=port, retry_seconds=0)
    try:
        assert b.port == port
    finally:
        b.stop()


def test_preferred_port_field_is_the_golden_pair():
    """stdin 字段与生产方（`src-tauri/src/sidecar/port_memory.rs`）的严格同源对：两侧各读 golden。"""
    golden = json.loads(GOLDEN_PREFERRED_PORT.read_text(encoding="utf-8"))
    assert desktop.PREFERRED_PORT_FIELD == golden["field"]
    assert (desktop.PREFERRED_PORT_MIN, desktop.PREFERRED_PORT_MAX) == (
        golden["valid_min"],
        golden["valid_max"],
    )
    # 壳首次挑选的范围必须落在消费方接受的范围里
    assert golden["valid_min"] <= golden["first_choice_min"] <= golden["first_choice_max"]
    assert golden["first_choice_max"] <= golden["valid_max"]
    for value in golden["accepted"]:
        line = json.dumps({"nonce": "n", golden["field"]: value}) + "\n"
        _, _, preferred, _ = desktop.read_launch_credentials(stdin=io.StringIO(line), environ={})
        assert preferred == value, value
    for value in golden["ignored"]:
        line = json.dumps({"nonce": "n", golden["field"]: value}) + "\n"
        nonce, _, preferred, _ = desktop.read_launch_credentials(
            stdin=io.StringIO(line), environ={}
        )
        assert preferred is None, value
        assert nonce == "n"  # 非法的建议端口只被忽略，不连累凭据


# ---- 真进程：两次启动落在同一端口（含服务端 TIME_WAIT）--------------------------
def _spawn_sidecar(tmp: Path, preferred_port: int | None) -> tuple[subprocess.Popen, Path]:
    """按壳的协议起一个真 `--desktop-sidecar` 进程（stdin 首行 JSON；数据 / 配置目录隔离）。"""
    tmp.mkdir(parents=True, exist_ok=True)
    handshake = tmp / "handshake.json"
    src_root = Path(desktop.__file__).resolve().parents[1]  # 被测的这棵源码树，不是别处装的 tavotto
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            p for p in (str(src_root), os.environ.get("PYTHONPATH", "")) if p
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
        "TAVOTTO_DATA_DIR": str(tmp / "data"),
        "TAVOTTO_CONFIG_DIR": str(tmp / "config"),
        "TAVOTTO_NO_TELEMETRY": "1",
        "TAVOTTO_DESKTOP_HANDSHAKE": str(handshake),
    }
    env.pop("TAVOTTO_DESKTOP_NONCE", None)
    with (tmp / "sidecar.log").open("wb") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tavotto", "--desktop-sidecar"],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(tmp),
        )
    hello: dict = {"nonce": NONCE, "parent_pid": os.getpid()}
    if preferred_port is not None:
        hello[desktop.PREFERRED_PORT_FIELD] = preferred_port
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(hello).encode() + b"\n")
    proc.stdin.flush()
    return proc, handshake


def _await_handshake(proc: subprocess.Popen, handshake: Path) -> dict:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if handshake.is_file():
            try:
                return json.loads(handshake.read_text(encoding="utf-8"))
            except ValueError:
                pass
        if proc.poll() is not None:
            log = (handshake.parent / "sidecar.log").read_text(encoding="utf-8", errors="replace")
            pytest.fail(f"sidecar 提前退出 {proc.returncode}:\n{log[-3000:]}")
        time.sleep(0.05)
    pytest.fail("等握手超时")


def _stop_via_stdin_eof(proc: subprocess.Popen) -> None:
    """壳退出的样子：关 stdin → sidecar 自己收摊退出。"""
    assert proc.stdin is not None
    proc.stdin.close()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        pytest.fail("stdin EOF 之后 sidecar 没有自己退出")


def _recv_until(sock: socket.socket, marker: bytes) -> bytes:
    buf = b""
    while marker not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def _plain_bind_is_blocked(port: int) -> bool:
    """POSIX 上不带 `SO_REUSEADDR` 的 bind 被挡 = 端口上确实留着服务端的 TIME_WAIT。"""
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return True
        return False


def _server_side_time_wait(port: int) -> list[str]:
    """本地端口 = `port`（即服务端那一侧）处于 TIME_WAIT 的 TCP 连接，回证据行（空 = 没有）。

    主语是服务端：客户端先关时 TIME_WAIT 落在客户端的临时端口上（本地地址不是 `port`），
    不算。Linux 用 `ss`，Windows 用 `netstat -ano`（列：`TCP 本地 远端 TIME_WAIT PID`）。
    macOS 的 `netstat` 实测列不出本进程造出来的回环 TIME_WAIT（2026-09-29 本机：普通 bind 被挡、
    netstat 一行都没有），改用「监听者已经退出、不带 `SO_REUSEADDR` 的普通 bind 仍被挡」作证据
    ——端口上没有任何监听者时只有残留连接态会挡它。命令跑不起来就让用例失败：「量不到」
    不能被读成「没有」。
    """
    if sys.platform.startswith("linux"):
        out = subprocess.run(
            ["ss", "-tanH", "state", "time-wait"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        ).stdout
        rows = [line.split() for line in out.splitlines() if line.strip()]
        # 指定了 state 时没有 State 列：Recv-Q Send-Q Local Peer
        return [" ".join(r) for r in rows if len(r) >= 4 and r[2] == f"127.0.0.1:{port}"]
    if os.name == "nt":
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # 只比 ASCII 的地址与状态列；本地化的代码页字节不影响它们
            check=True,
        ).stdout
        hits = []
        for line in out.splitlines():
            cols = line.split()
            if len(cols) >= 4 and cols[3] == "TIME_WAIT" and cols[1] == f"127.0.0.1:{port}":
                hits.append(line.strip())
        return hits
    return [f"plain bind 127.0.0.1:{port} blocked"] if _plain_bind_is_blocked(port) else []


def _bootstrap(port: int) -> str:
    status, headers, _ = http_post_json(
        f"http://127.0.0.1:{port}{desktop.BOOTSTRAP_PATH}", {"nonce": NONCE}
    )
    assert status == 200
    return headers["Set-Cookie"].split(";", 1)[0]


def test_two_launches_land_on_the_same_port_after_the_real_exit_path(tmp_path):
    """产品真实的退出路径：窗口开着 SSE 长连接（`/api/events`）时壳关 stdin（关窗 / 更新重启），
    第二次启动拿到同一个端口——origin 不变，localStorage 不丢。

    这条路上服务端那一侧**留不留** TIME_WAIT 由平台决定（Windows 上 sidecar 退出时开着的连接
    常被 RST 掉，WinError 10054），这条不管；「确有服务端 TIME_WAIT 时能不能重绑」是下一条的主语。
    """
    port = _free_port()
    first, hs_path = _spawn_sidecar(tmp_path / "first", port)
    try:
        hs = _await_handshake(first, hs_path)
        assert hs["ready"] is True and hs["port"] == port
        cookie = _bootstrap(port)
        sse = socket.create_connection(("127.0.0.1", port), timeout=15)
        try:
            sse.sendall(
                (
                    f"GET /api/events HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                    f"Cookie: {cookie}\r\nAccept: text/event-stream\r\n\r\n"
                ).encode()
            )
            assert b": connected" in _recv_until(sse, b": connected")
            _stop_via_stdin_eof(first)
            try:
                while sse.recv(65536):
                    pass
            except ConnectionResetError:
                pass  # Windows：退出时开着的连接被 RST，同样是「服务端走了」
        finally:
            sse.close()
    finally:
        if first.poll() is None:
            first.kill()
            first.wait()

    second, hs_path = _spawn_sidecar(tmp_path / "second", port)
    try:
        hs = _await_handshake(second, hs_path)
        assert hs["ready"] is True
        assert hs["port"] == port, "第二次启动没拿到同一个端口：origin 变了，localStorage 全丢"
    finally:
        _stop_via_stdin_eof(second)


def test_second_launch_rebinds_the_port_while_a_server_side_time_wait_is_there(tmp_path):
    """**显式造出**服务端 TIME_WAIT，再启动第二个 sidecar，断言重绑到同一端口。

    造法：对一条普通请求带 `Connection: close`，服务端回完就关（服务端是主动关闭方）；客户端
    读到 EOF 再关。先用 `ss` / `netstat` 断言这个端口上**确有**本地端口 = 它的 TIME_WAIT——
    前提没造出来（例如被 RST）就显式失败，不当绿、不 skip。POSIX 上再加一道：不带
    `SO_REUSEADDR` 的普通 bind 被挡（证明 TIME_WAIT 真的会挡 bind，这条量的正是它）。
    Windows 上 `SO_EXCLUSIVEADDRUSE` 碰上 TIME_WAIT 能不能立刻重绑，由 CI Windows 腿上这条回答。
    """
    port = _free_port()
    first, hs_path = _spawn_sidecar(tmp_path / "first", port)
    try:
        hs = _await_handshake(first, hs_path)
        assert hs["ready"] is True and hs["port"] == port
        for _ in range(3):  # 多造几条，别让一条的偶然（被 RST）决定前提
            c = socket.create_connection(("127.0.0.1", port), timeout=15)
            try:
                c.sendall(
                    (
                        f"GET {desktop.BOOTSTRAP_PATH} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                        "Connection: close\r\n\r\n"
                    ).encode()
                )
                while c.recv(65536):  # 读到 EOF：服务端先关
                    pass
            finally:
                c.close()
        _stop_via_stdin_eof(first)
    finally:
        if first.poll() is None:
            first.kill()
            first.wait()

    tw = _server_side_time_wait(port)
    assert tw, (
        f"前提未成立：端口 {port} 上没有服务端一侧的 TIME_WAIT（服务端没有主动关闭，或连接被 RST），"
        "这条用例量不到它要量的东西"
    )
    if os.name != "nt":
        assert _plain_bind_is_blocked(port), "前提未成立：TIME_WAIT 在，但普通 bind 没被挡"

    second, hs_path = _spawn_sidecar(tmp_path / "second", port)
    try:
        hs = _await_handshake(second, hs_path)
        assert hs["ready"] is True
        assert hs["port"] == port, (
            f"服务端 TIME_WAIT 在时第二次启动没拿到同一个端口（拿到 {hs['port']}）：{tw}"
        )
    finally:
        _stop_via_stdin_eof(second)


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
    status, _, body = http_post_json(sidecar.url(desktop.BOOTSTRAP_PATH), {"nonce": "wrong"})
    assert status == 403 and json.loads(body)["code"] == "bad_nonce"

    cookie = bootstrap_cookie(sidecar)

    # 重放同一 nonce → 403（一次性）
    status, _, _ = http_post_json(sidecar.url(desktop.BOOTSTRAP_PATH), {"nonce": NONCE})
    assert status == 403

    # 有 cookie → 放行到业务层：没开项目时 409 no_project；全量测试里其他用例
    # 可能已把默认项目打开（进程级状态），那时是 200——两者都证明穿过了认证
    status, _, body = http_get(sidecar.url("/api/panels"), headers={"Cookie": cookie})
    assert status in (200, 409)
    if status == 409:
        assert json.loads(body)["code"] == "no_project"

    # 伪造 cookie → 401
    status, _, _ = http_get(
        sidecar.url("/api/panels"), headers={"Cookie": f"{desktop.COOKIE_NAME}=forged"}
    )
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
def _raw_request(
    port: int, path: str, host: str, origin: str | None = None, cookie: str | None = None
):
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
    status, body = _raw_request(sidecar.port, "/api/version", host=f"localhost:{sidecar.port}")
    assert status == 403


def test_origin_check(sidecar):
    cookie = bootstrap_cookie(sidecar)
    good = f"127.0.0.1:{sidecar.port}"
    status, body = _raw_request(
        sidecar.port, "/api/version", host=good, origin="http://evil.example", cookie=cookie
    )
    assert status == 403 and body["code"] == "bad_origin"
    status, _ = _raw_request(
        sidecar.port, "/api/version", host=good, origin=f"http://{good}", cookie=cookie
    )
    assert status == 200


# ---------------------------------------------------------------------------
# updater 在桌面模式下停用
# ---------------------------------------------------------------------------
def test_updater_disabled_in_desktop(sidecar):
    cookie = bootstrap_cookie(sidecar)
    status, _, body = http_get(sidecar.url("/api/update/check"), headers={"Cookie": cookie})
    assert status == 200
    data = json.loads(body)
    assert data["desktop"] is True and data["update_available"] is False

    status, _, body = http_post_json(
        sidecar.url("/api/update/apply"), {}, headers={"Cookie": cookie}
    )
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
    env = {"TAVOTTO_DESKTOP_NONCE": "env-nonce", "TAVOTTO_DESKTOP_PARENT_PID": "4242"}
    nonce, pid, preferred, _ = desktop.read_launch_credentials(stdin=io.StringIO(""), environ=env)
    assert nonce == "env-nonce" and pid == 4242
    assert preferred is None  # 建议端口只从 stdin 首行来
    assert "TAVOTTO_DESKTOP_NONCE" not in env  # 用后即焚，不让子进程继承


def test_read_credentials_from_stdin_line():
    stream = io.StringIO(
        json.dumps({"nonce": "stdin-nonce", "parent_pid": 77, "preferred_port": 24680}) + "\n"
    )
    nonce, pid, preferred, returned = desktop.read_launch_credentials(stdin=stream, environ={})
    assert nonce == "stdin-nonce" and pid == 77 and preferred == 24680
    assert returned is stream  # 首行之后的流留给父进程监视


def test_read_credentials_tty_never_blocks():
    class Tty(io.StringIO):
        def isatty(self):
            return True

        def readline(self, *a):  # pragma: no cover - 若被调用即失败
            raise AssertionError("tty stdin 不应被读取（会无限阻塞）")

    nonce, pid, preferred, _ = desktop.read_launch_credentials(stdin=Tty(), environ={})
    assert nonce is None and pid is None and preferred is None


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
    files = [root / "engine" / n for n in ("pool.py", "bootstrap.py", "ai_bridge.py")] + [
        root / "app.py"
    ]
    missing = []
    for path in files:
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"(?:subprocess|sp)\.(?:run|Popen)\(", src):
            window = src[m.start() : m.start() + 700]
            for required in ("creationflags", "stdin="):
                if required not in window:
                    line = src[: m.start()].count("\n") + 1
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
