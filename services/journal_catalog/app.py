"""WSGI 适配层（不读环境变量，测试可直接用）。"""

from __future__ import annotations

from http import HTTPStatus

from .core import Service


class _Body:
    """WSGI 响应体：`close()` 由服务器在响应送出之后调用，统计类的收尾（索引计数）放在这里，不在请求关键路径上。
    收尾里任何异常都吞掉——统计失败不能影响已经送出的响应，更不能让服务器报错。"""

    def __init__(self, body: bytes, after: list):
        self._body = body
        self._after = after

    def __iter__(self):
        yield self._body

    def close(self) -> None:
        after, self._after = self._after, []
        for job in after:
            try:
                job()
            except Exception:  # noqa: BLE001 — 见类说明
                pass


def make_application(service: Service):
    def application(environ, start_response):
        headers = {
            "x-tavotto-device": environ.get("HTTP_X_TAVOTTO_DEVICE"),
            "x-tavotto-client": environ.get("HTTP_X_TAVOTTO_CLIENT"),
            "x-forwarded-for": environ.get("HTTP_X_FORWARDED_FOR"),
            "x-tavotto-proxy-secret": environ.get("HTTP_X_TAVOTTO_PROXY_SECRET"),
            "x-tavotto-client-ip": environ.get("HTTP_X_TAVOTTO_CLIENT_IP"),
            "if-none-match": environ.get("HTTP_IF_NONE_MATCH"),
        }
        after: list = []
        status, out_headers, body = service.handle(
            environ.get("REQUEST_METHOD", "GET"),
            environ.get("PATH_INFO", "/"),
            environ.get("QUERY_STRING", ""),
            headers,
            environ.get("REMOTE_ADDR"),
            after,
        )
        start_response(
            f"{status} {HTTPStatus(status).phrase}",
            [*out_headers, ("Content-Length", str(len(body)))],
        )
        return _Body(body, after)

    return application
