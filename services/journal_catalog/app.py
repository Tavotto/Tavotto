"""WSGI 适配层（不读环境变量，测试可直接用）。"""

from __future__ import annotations

from http import HTTPStatus

from .core import Service


def make_application(service: Service):
    def application(environ, start_response):
        headers = {
            "x-tavotto-device": environ.get("HTTP_X_TAVOTTO_DEVICE"),
            "x-tavotto-client": environ.get("HTTP_X_TAVOTTO_CLIENT"),
            "x-forwarded-for": environ.get("HTTP_X_FORWARDED_FOR"),
            "x-tavotto-proxy-secret": environ.get("HTTP_X_TAVOTTO_PROXY_SECRET"),
            "x-tavotto-client-ip": environ.get("HTTP_X_TAVOTTO_CLIENT_IP"),
        }
        status, out_headers, body = service.handle(
            environ.get("REQUEST_METHOD", "GET"),
            environ.get("PATH_INFO", "/"),
            environ.get("QUERY_STRING", ""),
            headers,
            environ.get("REMOTE_ADDR"),
        )
        start_response(
            f"{status} {HTTPStatus(status).phrase}",
            [*out_headers, ("Content-Length", str(len(body)))],
        )
        return [body]

    return application
