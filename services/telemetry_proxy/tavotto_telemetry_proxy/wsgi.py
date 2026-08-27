"""**唯一**的服务入口：与平台无关的 WSGI 应用，外加一个
`python -m tavotto_telemetry_proxy.wsgi` 起的本地开发服务器。

Vercel 直接跑这个 `application`（`pyproject.toml` 的 `[tool.vercel] entrypoint`），
本地开发跑同一个，测试也调同一个——**没有第二个入口层**。

## 为什么不是 `api/` 文件路由 + rewrites（2026-08-20 部署时踩过）

Vercel 的内部 rewrite 用**重写后的 destination** 路由：`/v1/events` 被重写到
`/api/index` 之后，函数拿到的 `self.path` 是 `/api/index`，原始路径取不到。
靠请求路径路由的写法因此**本地全绿、一部署整站 404**，而且那个 404 是我们
自己的代码返回的，看起来像 rewrite 没配或函数没起来。

WSGI 这条路没有这个问题：`PATH_INFO` 就是原始请求路径，没有中间的重写。
教训不只是「换个方案」——是**测试必须覆盖真实入口层**，只测 `core.handle`
的话入口层错成什么样都看不出来（见 tests/test_telemetry_proxy.py 末节）。
"""

from __future__ import annotations

import json
import os
import socketserver
from http import HTTPStatus
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from .core import handle


def application(environ, start_response):
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length < 0:
        # 负的 Content-Length 是畸形请求。不拦的话 min(length, MAX+1) 还是负数，
        # 某些 WSGI 服务器把 read(-1) 当成「读到 EOF」——keep-alive 连接上这一读
        # 会挂到对端超时，一个畸形请求就占死一个线程（PR #21 评审指出）。
        payload = json.dumps({"error": "invalid content-length"}).encode("utf-8")
        start_response(
            "400 Bad Request",
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(payload))),
                ("Cache-Control", "no-store"),
            ],
        )
        return [payload]
    # 多读一个字节：让 core 能把「超限」和「刚好到限」分开
    from .core import MAX_METRICS_BODY

    raw = environ["wsgi.input"].read(min(length, MAX_METRICS_BODY + 1)) if length else b""
    headers = {
        "content-type": environ.get("CONTENT_TYPE", ""),
        "authorization": environ.get("HTTP_AUTHORIZATION"),
    }
    status, body = handle(
        environ.get("REQUEST_METHOD", "GET"), environ.get("PATH_INFO", "/"), headers, raw
    )
    payload = json.dumps(body).encode("utf-8")
    # WSGI 规范要的是 `"200 OK"` 这种「码 + 原因短语」，不是光一个数字：
    # 有的服务器容忍，有的直接拒，而那种失败只会在部署之后出现。
    start_response(
        f"{status} {HTTPStatus(status).phrase}",
        [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(payload))),
            ("Cache-Control", "no-store"),
        ],
    )
    return [payload]


class _QuietHandler(WSGIRequestHandler):
    """**不打访问日志。**

    `wsgiref` 默认把 `<客户端 IP> - - [时间] "GET /healthz" 200` 打到 stderr。
    自己起 server 的场景（本地调试、腾讯云 SCF 的 Web 函数）里那会直接进云日志
    服务——等于**我们自己主动**记了一份带来源地址的访问日志，而
    `docs/privacy.md` 承诺的是「不刻意记录来源地址」。

    托管方自身的访问日志不归我们控制（政策里如实写着这一点，不做证明不了的
    承诺），但我们不再往上叠一份。
    """

    def log_message(self, *_args) -> None:
        return

    def address_string(self) -> str:
        # 连「拿一下对端地址」都不做：日志关了它也不该被求值
        return "-"


class _ThreadingWSGIServer(socketserver.ThreadingMixIn, WSGIServer):
    """每个请求一个线程。

    `wsgiref` 默认单线程：一个请求卡在上游超时（最长 5 秒）时后面的全部排队，
    于是「丢一条事件」变成「丢一串事件」，连健康检查也跟着超时——那会让托管
    平台以为实例挂了。`daemon_threads` 让实例被回收时不等在飞的请求。
    """

    daemon_threads = True


def main() -> None:  # pragma: no cover - 本地调试与 FaaS 都走它
    """起一个 HTTP server 跑 `application`。

    本地调试、腾讯云 SCF 的 **Web 函数**、以及任何「给我一个监听端口的进程」的
    托管方式共用这一个入口；Vercel 那种直接吃 WSGI 的则根本不经过它。

    **默认只听回环**：对外监听必须显式 `HOST=0.0.0.0`。少了这条默认值，
    本地调试时一不留神就会把一个无鉴权的公开端点暴露到局域网里。
    """
    host = os.environ.get("HOST") or "127.0.0.1"
    # SCF 的 Web 函数**限定 9000 端口**；本地随便挑
    port = int(os.environ.get("PORT") or 8787)
    print(f"* telemetry proxy on http://{host}:{port}", flush=True)
    make_server(
        host, port, application, server_class=_ThreadingWSGIServer, handler_class=_QuietHandler
    ).serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
