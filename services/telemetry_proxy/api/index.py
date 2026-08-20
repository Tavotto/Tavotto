"""Vercel Python Function 的**薄**入口。

这里只做协议搬运：把 `BaseHTTPRequestHandler` 的请求拍平成
(method, path, headers, body)，交给与平台无关的 `core.handle`，再把结果写回去。
校验、白名单、转发一行都不在这儿——换个托管方只需要重写这个文件。

Vercel 按 `/api` 下的文件路由，`vercel.json` 里把 `/healthz`、`/v1/*` 全部
重写到这一个函数上（见同目录 README）。
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tavotto_telemetry_proxy.core import MAX_METRICS_BODY, handle  # noqa: E402


class handler(BaseHTTPRequestHandler):          # noqa: N801 - Vercel 认这个名字
    def _run(self, method: str) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(min(length, MAX_METRICS_BODY + 1)) if length else b""
        headers = {
            "content-type": self.headers.get("Content-Type", ""),
            "authorization": self.headers.get("Authorization"),
        }
        status, body = handle(method, self.path, headers, raw)
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:                   # noqa: N802 - BaseHTTPRequestHandler 契约
        self._run("GET")

    def do_POST(self) -> None:                  # noqa: N802
        self._run("POST")

    def log_message(self, *_args) -> None:
        """**不写访问日志**：默认实现会把请求行与来源地址打到 stderr。

        托管方自己的访问日志不归我们控制（隐私政策里如实写着），但至少
        我们自己不主动再记一份带 IP 的。
        """
        return
