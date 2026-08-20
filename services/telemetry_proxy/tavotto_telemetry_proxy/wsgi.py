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
from http import HTTPStatus

from .core import handle


def application(environ, start_response):
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    # 多读一个字节：让 core 能把「超限」和「刚好到限」分开
    from .core import MAX_METRICS_BODY
    raw = environ["wsgi.input"].read(min(length, MAX_METRICS_BODY + 1)) if length else b""
    headers = {
        "content-type": environ.get("CONTENT_TYPE", ""),
        "authorization": environ.get("HTTP_AUTHORIZATION"),
    }
    status, body = handle(environ.get("REQUEST_METHOD", "GET"),
                          environ.get("PATH_INFO", "/"), headers, raw)
    payload = json.dumps(body).encode("utf-8")
    # WSGI 规范要的是 `"200 OK"` 这种「码 + 原因短语」，不是光一个数字：
    # 有的服务器容忍，有的直接拒，而那种失败只会在部署之后出现。
    start_response(f"{status} {HTTPStatus(status).phrase}",
                   [("Content-Type", "application/json"),
                    ("Content-Length", str(len(payload))),
                    ("Cache-Control", "no-store")])
    return [payload]


def main() -> None:                     # pragma: no cover - 本地调试用
    import os
    from wsgiref.simple_server import make_server

    port = int(os.environ.get("PORT") or 8787)
    print(f"* telemetry proxy on http://127.0.0.1:{port}")
    make_server("127.0.0.1", port, application).serve_forever()


if __name__ == "__main__":              # pragma: no cover
    main()
