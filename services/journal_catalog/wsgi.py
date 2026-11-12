"""唯一入口：与平台无关的 WSGI 应用（gunicorn / uwsgi / 本地开发服务器共用）。

`application` 在导入时按环境变量构造——缺 `TAVOTTO_JC_IP_SECRET` 等配置就在
导入时抛 `ConfigError`，进程起不来（拒绝启动），而不是每个请求都 500。

每日清理：`python -m services.journal_catalog.wsgi --purge`（见 README / DEPLOYMENT）。
"""

from __future__ import annotations

import os
import socketserver
import sys
from collections.abc import Mapping
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from .app import make_application
from .core import Service, config_from_env


def build_from_env(env: Mapping[str, str] | None = None) -> Service:
    return Service(config_from_env(os.environ if env is None else env))


class _QuietHandler(WSGIRequestHandler):
    """不打访问日志：不往日志里写来源地址与路径。"""

    def log_message(self, *_args) -> None:
        return

    def address_string(self) -> str:
        return "-"


class _ThreadingWSGIServer(socketserver.ThreadingMixIn, WSGIServer):
    daemon_threads = True


_service = build_from_env()
application = make_application(_service)


def main(argv: list[str]) -> None:  # pragma: no cover - 本地调试 / cron
    if "--purge" in argv:
        # 只打印数量，不含任何令牌 / IP / 刊 id
        print(_service.purge(), flush=True)
        return
    host = os.environ.get("HOST") or "127.0.0.1"
    port = int(os.environ.get("PORT") or 8788)
    make_server(
        host, port, application, server_class=_ThreadingWSGIServer, handler_class=_QuietHandler
    ).serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:])
