"""本机 WSGI server：bind 与 listen 之间不做名字解析（`tavotto/localserver.py`）。

判据的主语，三条各一个：

* ① **`LocalWSGIServer` 的构造路径**上有没有调 `socket.getfqdn`——把它换成会抛的
  函数，构造成功且 listen 已发生（connect 立刻成功，不是 timed out）才算过。
  拿掉 `server_bind` 覆写就回到 `http.server.HTTPServer` 那条路，当场红。
* ② **桌面 sidecar 的构造路径**（`desktop.SidecarServer`）同一问——它是另一处
  起 server 的地方，光看浏览器模式那处不够。
* ③ **真产品进程从 spawn 到 `/api/version` 应答的时间**：用 `sitecustomize` 把子进程
  的 `socket.getfqdn` 换成「记一笔再睡 20 s」，跑真的 `python -m tavotto --port P
  --no-browser`。就绪必须早于那 20 s，且启动到就绪之间 `getfqdn` 一次都没被调。
  本机 DNS 快、看不出差别，所以注入的是**延迟**而不是指望环境慢——修复前这条
  用例红在「20 s 之后才就绪」，修复后 1–2 s 就绪。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tavotto import app as appmod, desktop, localserver

REPO = Path(__file__).resolve().parent.parent

#: ③ 注入到子进程 `socket.getfqdn` 的延迟。就绪必须早于它——阈值就是它本身：
#: 反查一旦在 bind → listen 之间，就绪时间 = 冷启动 + 这个数，必然越界。
INJECTED_DNS_DELAY_S = 20.0


def _poisoned_getfqdn(*_a, **_k):
    raise AssertionError("bind 与 listen 之间不许解析主机名（socket.getfqdn 被调了）")


def _connects_immediately(port: int) -> bool:
    """端口已经 listen：connect 立刻成功。已 bind 未 listen 在 macOS 上是 timed out。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# ① 类本身
# ---------------------------------------------------------------------------
def test_the_server_never_resolves_a_hostname_between_bind_and_listen(monkeypatch):
    monkeypatch.setattr(socket, "getfqdn", _poisoned_getfqdn)
    srv = localserver.LocalWSGIServer("127.0.0.1", 0, appmod.app)
    try:
        # HTTPServer.server_bind 会填的两个属性照样在，只是 server_name 不再是反查结果
        assert srv.server_name == "127.0.0.1"
        assert srv.server_port == srv.server_address[1] > 0
        assert _connects_immediately(srv.server_port), "构造返回时必须已经 listen"
    finally:
        srv.server_close()


def test_the_server_keeps_werkzeug_threaded_semantics(monkeypatch):
    """换的只是 server_bind：线程模型、HTTP/1.1、handler、错误直通都是父类的。"""
    monkeypatch.setattr(socket, "getfqdn", _poisoned_getfqdn)
    srv = localserver.LocalWSGIServer("127.0.0.1", 0, appmod.app)
    try:
        assert srv.multithread and srv.daemon_threads
        assert srv.RequestHandlerClass.__name__ == "WSGIRequestHandler"
        assert srv.RequestHandlerClass.protocol_version == "HTTP/1.1"
        assert srv.passthrough_errors is False
        assert srv.ssl_context is None
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        with urllib.request.urlopen(
            f"http://127.0.0.1:{srv.server_port}/api/version", timeout=5
        ) as resp:
            assert resp.status == 200
        srv.shutdown()
        thread.join(timeout=5)
    finally:
        srv.server_close()


# ---------------------------------------------------------------------------
# ② 桌面 sidecar 的构造路径
# ---------------------------------------------------------------------------
def test_the_desktop_sidecar_builds_its_server_without_resolving_a_hostname(monkeypatch, tmp_path):
    monkeypatch.setattr(socket, "getfqdn", _poisoned_getfqdn)
    state = desktop.DesktopState("test-nonce-0123456789abcdef")
    srv = desktop.SidecarServer(appmod.app, state, tmp_path / "handshake.json")
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        assert srv.port == state.port > 0
        assert _connects_immediately(srv.port)
    finally:
        srv.shutdown()
        srv.wait_stopped(timeout=10)
        thread.join(timeout=5)
    assert desktop.security.STATE_KEY not in appmod.app.config


# ---------------------------------------------------------------------------
# ③ 真产品进程
# ---------------------------------------------------------------------------
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _served_port(data_dir: Path, requested: int) -> int:
    """产品真用的端口：凭据文件 `session/port-<P>.json` 在 bind 之前写（ADR 0008），
    顺延时它的名字跟着变；还没写出来就先按请求的那个探。"""
    for f in (data_dir / "session").glob("port-*.json"):
        try:
            return int(f.stem.split("-", 1)[1])
        except ValueError:
            continue
    return requested


def test_the_product_listens_without_waiting_for_a_hostname_lookup(tmp_path):
    inject = tmp_path / "inject"
    inject.mkdir()
    calls = tmp_path / "getfqdn-calls.txt"
    loaded = tmp_path / "sitecustomize-loaded.txt"
    # sitecustomize：解释器起来就换掉 socket.getfqdn——先记一笔，再睡满延迟。
    # 记那一笔是为了让红有两种读法：「就绪太晚」和「反查被调了」分得开。
    # `loaded` 标记证明注入在子进程里真的生效了：没生效的话「一次都没被调」
    # 恒真，这条用例就成了空门禁。
    (inject / "sitecustomize.py").write_text(
        textwrap.dedent(
            f"""
            import socket, time
            open({str(loaded)!r}, "w").close()
            _orig = socket.getfqdn
            def _slow_getfqdn(name=""):
                with open({str(calls)!r}, "a", encoding="utf-8") as f:
                    f.write(repr(name) + "\\n")
                time.sleep({INJECTED_DNS_DELAY_S})
                return _orig(name)
            socket.getfqdn = _slow_getfqdn
            """
        ),
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(inject), str(REPO / "src")]),
        "TAVOTTO_DATA_DIR": str(data_dir),
        "TAVOTTO_CONFIG_DIR": str(config_dir),
        "TAVOTTO_NO_UPDATE_CHECK": "1",
        "TAVOTTO_NO_TELEMETRY": "1",
    }
    env.pop("TAVOTTO_INSECURE_NO_AUTH", None)
    port = _free_port()
    log = (tmp_path / "server-stdout.log").open("w", encoding="utf-8")
    spawned_at = time.monotonic()
    # 不用 PIPE：没人并发排空的话，日志写满 64 KiB 就把子进程堵死
    proc = subprocess.Popen(
        [sys.executable, "-m", "tavotto", "--port", str(port), "--no-browser"],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    ready_after: float | None = None
    try:
        deadline = spawned_at + INJECTED_DNS_DELAY_S + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"进程在就绪前退出，returncode={proc.returncode}")
            probe = _served_port(data_dir, port)
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{probe}/api/version", timeout=1
                ) as resp:
                    if resp.status == 200:
                        ready_after = time.monotonic() - spawned_at
                        break
            except (urllib.error.URLError, OSError, TimeoutError):
                time.sleep(0.1)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        log.close()

    tail = (tmp_path / "server-stdout.log").read_text(encoding="utf-8")[-2000:]
    # 红的时候这一行也要看得见（pytest 会把捕获的 stdout 附在失败报告里）
    print(f"[localserver] 产品从 spawn 到 /api/version 应答 {ready_after}s")
    assert loaded.exists(), f"sitecustomize 没有在子进程里生效，下面的判据全是空的；输出尾:\n{tail}"
    assert ready_after is not None, f"产品始终没有就绪；子进程输出尾:\n{tail}"
    assert not calls.exists(), (
        f"启动到就绪之间 socket.getfqdn 被调了 {len(calls.read_text().splitlines())} 次：\n"
        f"{calls.read_text()}"
    )
    assert ready_after < INJECTED_DNS_DELAY_S, (
        f"就绪用了 {ready_after:.1f}s，不早于注入的 {INJECTED_DNS_DELAY_S:.0f}s 反查延迟"
    )


# ---------------------------------------------------------------------------
# 端口占用的排他性（#650 / Codex #651 P1）：平台策略只在 `localserver.bind_options`
# ---------------------------------------------------------------------------
def test_the_bind_policy_is_exclusive_on_windows_and_reusable_on_posix():
    """Windows 上 `SO_REUSEADDR` 允许与正在 listen 的 socket 共用端口——两个同时 claim 都会成功，占端口就不再是
    裁判；所以要 listen 的 socket 绝不带它、改带 `SO_EXCLUSIVEADDRUSE`，探测两样都不带。POSIX 上带 `SO_REUSEADDR`
    （TIME_WAIT 不挡同端口重启，且它在 POSIX 上不允许与 listener 共用）。按名字判，任何平台都跑。"""
    assert localserver.bind_options("nt", exclusive=True) == ("SO_EXCLUSIVEADDRUSE",)
    assert localserver.bind_options("nt", exclusive=False) == ()
    for exclusive in (True, False):
        assert localserver.bind_options("posix", exclusive=exclusive) == ("SO_REUSEADDR",)
    # 服务端自己 bind 时也走这份策略，不让 socketserver 按类属性在 Windows 上也设 SO_REUSEADDR
    assert localserver.LocalWSGIServer.allow_reuse_address is False


def _opt(sock: socket.socket, name: str) -> int:
    return sock.getsockopt(socket.SOL_SOCKET, getattr(socket, name))


def test_claim_and_the_server_socket_carry_the_platform_policy(monkeypatch):
    """`claim()` 占下的 socket 带着本平台的选项；werkzeug 经 `fd=` 接管之后选项原样在（不重设、不重 bind）；
    Windows 上 `SO_REUSEADDR` 始终是 0。"""
    monkeypatch.setattr(socket, "getfqdn", lambda *a: pytest.fail("不许反查主机名"))
    sock = localserver.claim("127.0.0.1", 0)
    port = sock.getsockname()[1]
    srv = localserver.LocalWSGIServer("127.0.0.1", port, appmod.app, fd=sock.fileno())
    sock.close()
    try:
        assert srv.socket.getsockname()[1] == port
        for name in localserver.bind_options(exclusive=True):
            assert _opt(srv.socket, name), f"接管之后 {name} 丢了"
        if os.name == "nt":
            assert not _opt(srv.socket, "SO_REUSEADDR"), "Windows 上监听 socket 带了 SO_REUSEADDR"
    finally:
        srv.server_close()


def test_a_second_claim_on_a_claimed_port_fails():
    """两个同时启动的实例：先占到的赢，后来者 bind 失败（`app.claim_port` 据此重新判定、走复用）。
    Windows 上这条正是 Codex #651 P1 的主语——两边都带 SO_REUSEADDR 时第二个会成功。"""
    first = localserver.claim("127.0.0.1", 0)
    try:
        with pytest.raises(OSError):
            localserver.claim("127.0.0.1", first.getsockname()[1]).close()
    finally:
        first.close()


@pytest.mark.skipif(os.name != "nt", reason="SO_REUSEADDR 抢占已 listen 端口只在 Windows 上成立")
def test_a_reuseaddr_socket_cannot_take_a_claimed_port_on_windows():
    """`SO_EXCLUSIVEADDRUSE` 的那一半：别的 socket 即使带 `SO_REUSEADDR` 也 bind 不上已占的端口。"""
    first = localserver.claim("127.0.0.1", 0)
    try:
        with socket.socket() as other:
            other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError):
                other.bind(("127.0.0.1", first.getsockname()[1]))
    finally:
        first.close()


def test_a_claimed_port_can_be_claimed_again_right_after_the_last_instance_exits():
    """同端口重启（QA STATE-08-B1）：上一个实例 accept 过连接、由服务端先关（留下 TIME_WAIT），再关 listener——
    紧接着再 claim 同一个端口必须成功。POSIX 靠 SO_REUSEADDR；Windows 上排他 bind 不能因为这一刻而失败。
    前提断言防空转：连接确实走完了（读到 EOF）。"""
    first = localserver.claim("127.0.0.1", 0)
    port = first.getsockname()[1]
    cli = socket.create_connection(("127.0.0.1", port), timeout=5)
    conn, _ = first.accept()
    conn.close()  # 服务端主动关 → 它这一侧进 TIME_WAIT
    assert cli.recv(1) == b""
    cli.close()
    first.close()
    again = localserver.claim("127.0.0.1", port)
    again.close()
