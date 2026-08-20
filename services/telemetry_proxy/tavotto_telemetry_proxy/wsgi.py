"""与平台无关的 WSGI 适配层，外加一个 `python -m tavotto_telemetry_proxy.wsgi`
起的本地开发服务器。

它存在的意义是「不把核心逻辑绑死在某一家 serverless 上」：Vercel 的入口
（`api/index.py`）与这里都只做同一件事——把请求拍平成
(method, path, headers, body) 交给 `core.handle`。
"""
from __future__ import annotations

import json

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
    start_response(f"{status} ", [("Content-Type", "application/json"),
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
