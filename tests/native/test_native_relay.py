"""CLI 托管的认证 raw relay（ADR 0021 §3）。

两类判据：

* **认证**：只 loopback、两枚互不通用的 token、错 token 不消耗 listener、
  一次会话一条连接；
* **透明**：进去多少字节出来多少字节，一个 JSON 解析器都没有。
  这一条最容易在某次"顺手加个字段"里塌掉，所以判据里放了一条**任何 JSON
  解析器都会拒收**的畸形帧——relay 必须原样送过去。
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from tavotto.engine import nativerelay, runcodes
from tavotto.engine.runcodes import RunError


def connect(port: int, token: str, key: str = nativerelay.ATTACH_KEY):
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    sock.sendall(json.dumps({key: 1, "token": token}).encode("utf-8") + b"\n")
    return sock


def read_line(sock, limit: int = 65536) -> bytes:
    buf = bytearray()
    while len(buf) < limit:
        b = sock.recv(1)
        if not b or b == b"\n":
            break
        buf += b
    return bytes(buf)


@pytest.fixture
def relay():
    r = nativerelay.NativeRelay()
    try:
        yield r
    finally:
        r.close()


def both_sides(relay: nativerelay.NativeRelay, timeout: float = 15.0):
    """把两侧都接上（桌面先、子进程后——产品顺序就是这样）。"""
    box: dict = {}

    def _serve():
        try:
            box["desktop_hello"] = relay.wait_for_desktop(timeout)
            box["child_hello"] = relay.wait_for_child(timeout)
            relay.start_pump()
            box["ok"] = True
        except BaseException as exc:  # noqa: BLE001 — 线程里的失败要带回主线程
            box["error"] = exc

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    desktop = connect(relay.attach_port, relay.attach_token)
    assert json.loads(read_line(desktop))["ok"] is True
    child = connect(relay.child_port, relay.child_token, nativerelay.HELLO_KEY)
    assert json.loads(read_line(child))["ok"] is True
    t.join(timeout)
    assert box.get("ok"), f"relay 没接上: {box.get('error')}"
    # 桌面侧的第一帧是转发过来的子进程握手（见
    # `test_the_child_hello_reaches_the_desktop_without_its_token`）。
    # 这里把它读掉，后面的判据才量得到"转发的字节"本身。
    box["forwarded_hello"] = json.loads(read_line(desktop))
    return desktop, child, box


# --------------------------------------------------------------------------
def test_loopback_only(relay):
    """只 bind 127.0.0.1。**绝不** 0.0.0.0——那是把控制通道放到局域网上。"""
    for sock in (relay._attach_listener, relay._child_listener):  # noqa: SLF001
        assert sock.getsockname()[0] == "127.0.0.1"


def test_two_distinct_tokens(relay):
    """两侧各一枚，256 位，互不通用。

    共用一枚的表现是：桌面那枚一旦泄漏（它要经过 descriptor 文件），
    拿它就能冒充用户的 Python 连上去说 worker 协议。
    """
    assert relay.attach_token != relay.child_token
    assert len(relay.attach_token) >= 40 and len(relay.child_token) >= 40


def test_the_desktop_token_does_not_open_the_child_side(relay):
    box: dict = {}
    t = threading.Thread(
        target=lambda: box.setdefault("hello", _swallow(relay.wait_for_child, 2.0)), daemon=True
    )
    t.start()
    sock = connect(relay.child_port, relay.attach_token, nativerelay.HELLO_KEY)
    resp = json.loads(read_line(sock))
    assert resp["ok"] is False and resp["code"] == runcodes.NATIVE_AUTH_FAILED
    t.join(10)
    assert relay.rejected["child"] == 1


def test_wrong_token_does_not_consume_the_listener(relay):
    """认证失败**继续 accept**。收摊等于把 DoS 送出去：本机任何进程抢先连
    一下，用户的 `tavotto run` 就永远起不来。"""
    box: dict = {}

    def _serve():
        try:
            box["hello"] = relay.wait_for_desktop(15.0)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    for _ in range(3):
        bad = connect(relay.attach_port, "wrong-token")
        assert json.loads(read_line(bad))["ok"] is False
        bad.close()
    good = connect(relay.attach_port, relay.attach_token)
    assert json.loads(read_line(good))["ok"] is True
    t.join(15)
    assert box.get("hello") is not None, box.get("error")
    assert relay.rejected["desktop"] == 3
    good.close()


def test_second_attach_rejected(relay):
    """一次会话一条连接：成功之后那一侧的 listener 就关了。"""
    box: dict = {}
    t = threading.Thread(
        target=lambda: box.setdefault("hello", relay.wait_for_desktop(15.0)), daemon=True
    )
    t.start()
    first = connect(relay.attach_port, relay.attach_token)
    assert json.loads(read_line(first))["ok"] is True
    t.join(15)
    with pytest.raises(OSError):
        second = socket.create_connection(("127.0.0.1", relay.attach_port), timeout=2)
        second.sendall(b'{"native_attach":1,"token":"x"}\n')
        # 有些平台上 connect 会成功、随后立刻 EOF——两种都算"连不上"
        if not second.recv(1):
            raise OSError("listener 已关闭")
    first.close()


def test_the_child_hello_reaches_the_desktop_without_its_token(relay):
    """转发一次去掉 token 的握手帧——桌面要拿到 pid，但**不该拿到子进程那枚
    凭据**。这是本模块唯一会"构造"的字节。"""
    box: dict = {}

    def _serve():
        box["desktop"] = relay.wait_for_desktop(15.0)
        box["child"] = relay.wait_for_child(15.0)

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    desktop = connect(relay.attach_port, relay.attach_token)
    read_line(desktop)
    child = socket.create_connection(("127.0.0.1", relay.child_port), timeout=10)
    child.sendall(
        json.dumps(
            {
                nativerelay.HELLO_KEY: 1,
                "token": relay.child_token,
                "pid": 4242,
                "protocol_version": 1,
            }
        ).encode()
        + b"\n"
    )
    read_line(child)
    t.join(15)
    forwarded = json.loads(read_line(desktop))
    assert forwarded["pid"] == 4242
    assert "token" not in forwarded
    desktop.close()
    child.close()


def test_relay_is_byte_transparent(relay):
    """进去多少字节出来多少字节。

    判据里**故意放一条畸形帧**（未闭合的 JSON + 裸控制字符 + 一个巨大的
    行）：任何在中间解析一下的实现都会在这里塌掉，而"顺手 parse 一下再转发"
    正是这类模块最常见的退化方向。
    """
    desktop, child, _ = both_sides(relay)
    payloads = [
        b'{"protocol_version":1,"request_id":"a","cmd":"ping"}\n',
        '{"cmd":"render","label":"温度 µ⁻¹ ✓"}\n'.encode(),
        b'{"broken": [1, 2, \x01\x02 not json at all\n',
        b"x" * 200_000 + b"\n",
        b"\n\n\n",
    ]
    for p in payloads:
        desktop.sendall(p)
    got = _read_exactly(child, sum(len(p) for p in payloads))
    assert got == b"".join(payloads)

    back = [b'{"ok":true}\n', b"\xff\xfe binary-ish \x00\n"]
    for p in back:
        child.sendall(p)
    assert _read_exactly(desktop, sum(len(p) for p in back)) == b"".join(back)
    desktop.close()
    child.close()


def test_relay_sidecar_disconnect_closes_the_child_side(relay):
    """桌面走了 → 子进程侧立刻看到 EOF。

    不这样做的具体形状是：**用户的脚本卡在屏障上**，而他的终端上什么都
    不显示。
    """
    desktop, child, _ = both_sides(relay)
    desktop.close()
    child.settimeout(10)
    assert child.recv(4096) == b"", "桌面断开之后子进程侧没有收到 EOF"
    child.close()


def test_relay_child_disconnect_closes_the_desktop_side(relay):
    desktop, child, _ = both_sides(relay)
    child.close()
    desktop.settimeout(10)
    assert desktop.recv(4096) == b""
    desktop.close()


def test_relay_cleanup_releases_both_listeners():
    r = nativerelay.NativeRelay()
    ports = (r.attach_port, r.child_port)
    r.close()
    for port in ports:
        probe = socket.socket()
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))  # 端口真的还回去了才 bind 得上
        finally:
            probe.close()


def test_attach_timeout_has_a_stable_code(relay):
    with pytest.raises(RunError) as exc:
        relay.wait_for_desktop(0.3)
    assert exc.value.code == runcodes.NATIVE_ATTACH_TIMEOUT
    assert exc.value.exit_code() == runcodes.EXIT_ATTACH_FAILED


def test_a_watch_callback_can_abort_the_wait(relay):
    """子进程提前死了 / 用户点了取消——不该白等满整个 timeout。"""

    def _watch():
        raise RunError(runcodes.NATIVE_ATTACH_CANCELLED)

    t0 = time.monotonic()
    with pytest.raises(RunError) as exc:
        relay.wait_for_desktop(30.0, watch=_watch)
    assert exc.value.code == runcodes.NATIVE_ATTACH_CANCELLED
    assert time.monotonic() - t0 < 5.0, "watch 抛了却还在等"


def test_the_relay_never_imports_the_engine_semantics():
    """**结构性守卫**：relay 只搬字节，它不该认识 manifest / override /
    patch / figure / 协议信封里的任何一个概念。

    行为判据（上面那条透明性）能抓住今天的实现；这一条抓的是明天有人
    "顺手 import 一下 wireproto 校验校验"的那次改动。
    """
    src = __import__("pathlib").Path(nativerelay.__file__).read_text(encoding="utf-8")
    body = src.split('"""', 2)[-1]  # 模块 docstring 里会提到这些名字
    for forbidden in (
        "import manifest",
        "import overrides",
        "import patchspec",
        "import figsession",
        "import wireproto",
        "build_envelope",
        "canonical_patch_hash",
    ):
        assert forbidden not in body, f"relay 里出现了引擎语义: {forbidden!r}"


def _read_exactly(sock, n: int, timeout: float = 20.0) -> bytes:
    sock.settimeout(timeout)
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(65536, n - len(buf)))
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


def _swallow(fn, *args):
    try:
        return fn(*args)
    except BaseException:  # noqa: BLE001 — 这个线程只是为了让 accept 循环转起来
        return None
