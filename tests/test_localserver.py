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
