"""`tavotto run` CLI 托管的**认证 raw relay**（ADR 0021 §3）。

```text
Tavotto 桌面 sidecar ──attach listener──┐
                                        │  CLI（本模块）
用户 Python 的 Bridge Runner ─child listener──┘
                                        │
                            两条已认证连接之间的**透明字节转发**
```

## 为什么由 CLI 托管，而不是让子进程直连 sidecar

因为 CLI 必须继续拥有用户的 Python（ADR 0021 §1）：stdin/stdout/stderr、
cwd、env、argv、Ctrl+C 全都只有它拿得到。而一旦 CLI 是父进程，控制通道要么
从它身上过，要么就得把 **sidecar 的凭据**交到用户脚本所在的进程里——后者把
一枚 token 的作用域从"这一条 bridge 连接"放大到了"整个 sidecar"。

多出来的这一跳是**纯字节**：协议面零增加。

## relay 绝不做的事

* 解析 / 重写 manifest；
* 生成另一套 request envelope；
* 改 canonical patch；
* 碰 Figure；
* 造 native protocol v2。

**唯一被本模块解析的帧是握手帧**，而它只做两件事：核对 token、把子进程握手
帧里的 token 摘掉再转给桌面（桌面不该拿到子进程那枚凭据）。此后 `pump()`
里一个字节都不解释——看护：`test_relay_is_byte_transparent`（含一条故意
畸形、任何 JSON 解析器都会拒收的帧）+ 结构性守卫（本模块不许 import
`manifest` / `overrides` / `patchspec` / `figsession` / `wireproto`）。

## 握手为什么不用 `makefile`

`makefile("r").readline()` 会**预读**：缓冲区里可能已经躺着握手之后的字节，
而此后本模块按字节转发、再也不碰那个缓冲——那几个字节就永远丢了，表现是
"第一条请求没有响应"。握手帧很小，逐字节读到 `\n` 是唯一不会多读的做法。

## 认证

* 两侧 token **各一枚，互不通用**——桌面那枚泄漏也连不上子进程侧；
* 只 bind `127.0.0.1`，端口 0 让内核分配；
* 256-bit，`compare_digest` 比对；
* **认证失败不消耗 listener**：断开这一条、继续 accept。收摊等于把 DoS 送
  出去（本机任何进程抢先连一下，用户的 `tavotto run` 就永远起不来）；
* 一侧成功 attach 之后**关掉那一侧的 listener**：一次会话一条连接。

纯标准库。
"""

from __future__ import annotations

import json
import secrets
import socket
import threading
import time

from . import runcodes
from .runcodes import RunError

#: 桌面侧握手帧的键。
ATTACH_KEY = "native_attach"
#: 子进程侧握手帧的键——**与 `bridge_runner.HELLO_KEY` 严格同源**
#: （runner 是 ADR 0020 定稿的那份，本模块必须原样接住它）。
HELLO_KEY = "bridge_hello"

#: 转发缓冲。大到不为每条 render 响应多跑几十次 syscall，小到不至于让一条
#: 巨大的 inline SVG 把内存顶起来。
_CHUNK = 65536

#: 握手行的上限。超过就是"这不是我们的握手"——不给一条无限长的行任何机会
#: 把内存吃光（loopback 上也一样：本机的恶意进程连得上这个端口）。
_HELLO_MAX = 8192

#: accept / 握手读取的轮询粒度（要能及时看见 deadline 与外部 cancel）。
_POLL = 0.25


class RelayClosed(RuntimeError):
    """relay 已经收摊（正常结束或被 cancel）。"""


class NativeRelay:
    """一次 `tavotto run` 的控制面。**由 CLI 独占，不跨进程共享。**"""

    def __init__(self, *, host: str = "127.0.0.1"):
        self.host = host
        self.attach_token = secrets.token_urlsafe(32)
        self.child_token = secrets.token_urlsafe(32)
        self._attach_listener = self._listen()
        self._child_listener = self._listen()
        self.attach_port = self._attach_listener.getsockname()[1]
        self.child_port = self._child_listener.getsockname()[1]
        self.attach_sock: socket.socket | None = None
        self.child_sock: socket.socket | None = None
        #: 被拒绝的未认证连接数（诊断用；**按侧分开**）。
        self.rejected = {"desktop": 0, "child": 0}
        self._cancel = threading.Event()
        self._pumps: list[threading.Thread] = []
        self._closed = False
        #: 转发过的字节数（诊断用；**不记内容**）。
        self.bytes_to_child = 0
        self.bytes_to_desktop = 0

    def _listen(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, 0))  # **只** loopback，端口 0 让内核分配
        s.listen(4)
        return s

    # ------------------------------------------------------------------
    # 认证 accept
    # ------------------------------------------------------------------
    def _accept_authenticated(
        self,
        listener: socket.socket,
        token: str,
        *,
        side: str,
        timeout: float,
        hello_key: str,
        timeout_code: str,
        watch=None,
    ) -> tuple[socket.socket, dict]:
        """接受连接直到**认证通过**的那一条；返回 `(sock, 去掉 token 的握手帧)`。

        `watch` 是一个每轮调用一次的回调，**它自己抛**（`RunError`）来中止
        等待。两种用法：子进程提前退出（免得白等满整个 timeout——那时用户对着
        一个"正在等待"的提示，而他的 Python 早就报错退出了），以及用户在桌面
        上点了取消（descriptor 变成 cancelled 墓碑）。

        让 `watch` 自己抛而不是回一个码，是因为中止的**原因**只有调用方知道，
        而错误码要准（`bridge_child_exited` 与 `native_attach_cancelled` 的
        下一步动作完全不同）。
        """
        deadline = time.monotonic() + timeout
        while True:
            if self._cancel.is_set():
                raise RelayClosed("relay 已取消")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RunError(timeout_code, seconds=int(timeout), rejected=self.rejected[side])
            listener.settimeout(min(_POLL, remaining))
            try:
                conn, _ = listener.accept()
            except (TimeoutError, socket.timeout):
                if watch is not None:
                    watch()
                continue
            except OSError as exc:
                raise RelayClosed(f"listener 已关闭: {exc}") from exc
            hello = _read_hello(conn)
            claimed = hello.get("token") if isinstance(hello, dict) else None
            if not isinstance(claimed, str) or not secrets.compare_digest(claimed, token):
                self.rejected[side] += 1
                _quiet_send(
                    conn,
                    json.dumps({"ok": False, "code": runcodes.NATIVE_AUTH_FAILED}).encode() + b"\n",
                )
                _quiet_close(conn)
                continue  # **不停止 accept**：失败一次就收摊 = 把 DoS 送出去
            _quiet_send(conn, json.dumps({hello_key: 1, "ok": True}).encode("utf-8") + b"\n")
            conn.settimeout(None)
            hello.pop("token", None)  # token 不进任何可能被转发/打日志的结构
            return conn, hello

    def wait_for_desktop(self, timeout: float, *, watch=None) -> dict:
        """等桌面 sidecar 连上并认证。返回它的握手帧（不含 token）。"""
        conn, hello = self._accept_authenticated(
            self._attach_listener,
            self.attach_token,
            side="desktop",
            timeout=timeout,
            hello_key=ATTACH_KEY,
            timeout_code=runcodes.NATIVE_ATTACH_TIMEOUT,
            watch=watch,
        )
        self.attach_sock = conn
        _quiet_close(self._attach_listener)  # 一次会话一条连接
        return hello

    def wait_for_child(self, timeout: float, *, watch=None) -> dict:
        """等 Bridge Runner 连上并认证，并把它的握手帧转给桌面。

        **转发一次去掉 token 的握手帧**是本模块唯一会"构造"的字节。它不是
        新协议：桌面侧要拿到的正是 ADR 0020 里 `BridgeSession.start()` 返回
        的那一帧（pid / protocol_version）。token 必须在这里被摘掉——桌面
        不该持有子进程那枚凭据。
        """
        conn, hello = self._accept_authenticated(
            self._child_listener,
            self.child_token,
            side="child",
            timeout=timeout,
            hello_key=HELLO_KEY,
            timeout_code=runcodes.NATIVE_ATTACH_TIMEOUT,
            watch=watch,
        )
        self.child_sock = conn
        _quiet_close(self._child_listener)
        if self.attach_sock is not None:
            _quiet_send(
                self.attach_sock, json.dumps(hello, ensure_ascii=False).encode("utf-8") + b"\n"
            )
        return hello

    # ------------------------------------------------------------------
    # 转发
    # ------------------------------------------------------------------
    def start_pump(self) -> None:
        """开两个方向的字节转发线程。**这之后本模块不再解释任何字节。**"""
        if self.attach_sock is None or self.child_sock is None:
            raise RelayClosed("两侧都连上之后才能转发")
        self._pumps = [
            threading.Thread(
                target=self._pump,
                args=(self.attach_sock, self.child_sock, "to_child"),
                daemon=True,
                name="tavotto-relay-d2c",
            ),
            threading.Thread(
                target=self._pump,
                args=(self.child_sock, self.attach_sock, "to_desktop"),
                daemon=True,
                name="tavotto-relay-c2d",
            ),
        ]
        for t in self._pumps:
            t.start()

    def _pump(self, src: socket.socket, dst: socket.socket, which: str) -> None:
        """`src` → `dst` 的**纯字节**转发。一个 JSON 解析器都没有。"""
        try:
            while not self._cancel.is_set():
                chunk = src.recv(_CHUNK)
                if not chunk:
                    break
                dst.sendall(chunk)
                if which == "to_child":
                    self.bytes_to_child += len(chunk)
                else:
                    self.bytes_to_desktop += len(chunk)
        except OSError:
            pass
        finally:
            # 一侧断了就把另一侧也半关掉：让对端立刻看到 EOF，而不是永远等。
            # native 里"永远等"的具体形状是**用户的脚本卡在屏障上**，
            # 而他的终端上什么都不显示。
            with _suppress():
                dst.shutdown(socket.SHUT_WR)

    def wait_pumps(self, timeout: float | None = None) -> None:
        for t in self._pumps:
            t.join(timeout)

    # ------------------------------------------------------------------
    def cancel(self) -> None:
        """从别的线程中止还在等待的 accept（用户按了取消 / 收到信号）。"""
        self._cancel.set()
        _quiet_close(self._attach_listener)
        _quiet_close(self._child_listener)

    def close(self) -> None:
        """关掉整条 relay。**已连上的两条要先 `shutdown` 再 `close`。**

        少了 `shutdown` 的那一版在 macOS 上一直是绿的，在 Linux 上必挂——
        差别不在"慢"，在 `close()` 与阻塞中的 `recv()` 的语义：

        * **Linux**：`close(fd)` 立刻返回，但**不唤醒**另一个线程里阻塞着的
          `recv(fd)`；那个系统调用还持着底层的 file description，于是 FIN
          **不会被发出去**。两个 pump 线程此刻正好各自阻塞在一侧的 `recv`
          上（脚本停在屏障上，两边都没有字节可读），所以对端永远等不到 EOF。
        * **macOS / BSD**：`close()` 会让阻塞中的 `recv` 带 `EBADF` 返回，
          套接字随即拆掉、FIN 发出——**所以本机怎么跑都是对的**。

        这条路径上"对端永远等不到 EOF"的具体形状是：用户按了 Ctrl+C，脚本
        收到了、也打印了、也 `sys.exit(130)` 了，但 Bridge Runner 停在"脚本
        结束"那个屏障上等控制通道说话——通道没关，屏障不放，进程不退，
        用户的终端再也回不来。**Tavotto 改变了 Ctrl+C 的含义**，而这正是
        `_wait_for_child_process()` 那段注释里明写着不许发生的事。

        `shutdown(SHUT_RDWR)` 作用在**套接字**而不是 fd 表上：两个平台都会
        立刻发 FIN，并让阻塞中的 `recv` 返回 0（EOF）。`nativesession` 那边
        的 `Transport.close()` 一直是这么写的——这里是漏掉的第二个消费点。
        """
        if self._closed:
            return
        self._closed = True
        self._cancel.set()
        # 顺序是硬的：先让两条已连上的通道 EOF，再关 fd。反过来就是上面那个
        # Linux 死锁（fd 关了、FIN 还没发）。
        for s in (self.attach_sock, self.child_sock):
            _quiet_shutdown(s)
        for s in (self._attach_listener, self._child_listener, self.attach_sock, self.child_sock):
            _quiet_close(s)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def state(self) -> dict:
        """诊断用的一眼状态。**不含 token、不含转发内容。**"""
        return {
            "host": self.host,
            "desktop_attached": self.attach_sock is not None,
            "child_attached": self.child_sock is not None,
            "rejected": dict(self.rejected),
            "bytes_to_child": self.bytes_to_child,
            "bytes_to_desktop": self.bytes_to_desktop,
            "closed": self._closed,
        }


def _read_hello(conn: socket.socket) -> dict:
    """逐字节读一行握手 JSON。**绝不多读**（见模块头「握手为什么不用 makefile」）。

    读不出合法 JSON 时回 `{}`——调用方会因为 token 对不上而拒绝它，这与
    "发了个乱七八糟的东西"应有的结果一致。
    """
    conn.settimeout(15.0)
    buf = bytearray()
    try:
        while len(buf) < _HELLO_MAX:
            b = conn.recv(1)
            if not b:
                break
            if b == b"\n":
                break
            buf += b
    except OSError:
        return {}
    try:
        data = json.loads(buf.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _quiet_send(sock, data: bytes) -> None:
    with _suppress():
        sock.sendall(data)


def _quiet_close(obj) -> None:
    if obj is None:
        return
    with _suppress():
        obj.close()


def _quiet_shutdown(sock) -> None:
    """让对端立刻看到 EOF，并唤醒本进程里阻塞在这条 socket 上的 `recv`。

    没连上（`ENOTCONN`）或已经关了的一律吞掉——`close()` 是收尾路径，
    在那里抛异常只会盖住真正的失败原因。
    """
    if sock is None:
        return
    with _suppress():
        sock.shutdown(socket.SHUT_RDWR)


class _suppress:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None and issubclass(exc_type, (OSError, ValueError))
