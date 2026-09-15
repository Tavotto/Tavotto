#!/usr/bin/env python3
"""给 `scripts/ci/package_smoke.py` 的单测当**被起的进程**：一个起来时长得像 Tavotto 的 HTTP 桩。

    python tests/support/stub_http_server.py --port 51234 [--fail-mode MODE] \\
        [--state-file S] [--pid-file P] [--delay N]

像 Tavotto 的那几处（冒烟脚本的判据落在它们上面，所以桩必须照样做）：

* `/` 200、`/api/version` 200 + JSON `{"version": "stub", "build": "stub"}`；
* 在 bind 之前把本机凭据写进 `TAVOTTO_DATA_DIR`（**用产品自己的**
  `engine.session_client.publish_secret`，路径公式与文件形状不另写一份）；
* `/api/session/ping` 只在请求带着那枚 secret 的 `X-Tavotto-Auth` 时 200，否则 401；
* 起一个子进程（`sleep 600`）当「worker」——冒烟脚本的终止判据要连它一起消失；
  `--pid-file` 给了就把两个 pid 写进去，单测据此核「进程不存在」。

`--fail-mode`（每种对应冒烟脚本的一条负例）：

| 模式 | 行为 | 冒烟脚本应当 |
|---|---|---|
| `normal`（默认） | 正常服务 | rc 0 |
| `bind-busy` | **第一次**起（`--state-file` 还不存在）时在同一进程里把 `--port` bind 两遍，把平台真实的 EADDRINUSE 文案打到 stderr 后退 1；第二次起正常服务 | 认出租约丢了，换端口重试后 rc 0，attempts == 2 |
| `fallback` | 第一次起时打「* 端口 P 被占用，改用 Q」（产品 `resolve_port` 顺延时的那句）并在 Q 上服务；第二次正常 | 同上——租约丢了不必等到超时 |
| `never-ready` | `/api/version` 永远 503 | 超时 rc 1，stderr 带日志尾 |
| `slow-ready` | 起来 `--delay` 秒之后 `/api/version` 才 200 | rc 0 且 ready_seconds ≥ delay |
| `crash` | 启动即退 3，stderr 有一句 | rc 1，stderr 含那一句 |
| `no-credentials` | 正常服务但**不写**凭据文件（像隔壁实例：它的凭据在它自己的 data dir 里） | 不算就绪 → 超时 rc 1 |
| `reject-credentials` | 写了凭据文件，但 `/api/session/ping` 对任何 header 都 401（像端口上答话的是别人） | 不算就绪 → 超时 rc 1 |
| `bad-json` | `/api/version` 200 但 body 不是 JSON | 不算就绪 → 超时 rc 1 |
| `index-500` | 就绪判据全过，但 `/` 500 | rc 1，原因是 `GET / → 500` |
| `no-version-field` | `/api/version` 200 的 JSON 里没有 `version` | rc 1，原因是缺 version 字段 |

`--worker-ignores-term`（POSIX）：起的 worker 对 SIGTERM 置 SIG_IGN——冒烟脚本对整组 SIGTERM 之后
组里还剩它，必须升级到 SIGKILL 才算「进程不存在」。

stdout / stderr 钉 UTF-8：它被 `subprocess` 起、输出落文件，Windows 上不钉的话中文那句会先炸。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

MODES = (
    "normal",
    "bind-busy",
    "fallback",
    "never-ready",
    "slow-ready",
    "crash",
    "no-credentials",
    "reject-credentials",
    "bad-json",
    "index-500",
    "no-version-field",
)


def _first_start(state_file: Path | None) -> bool:
    """`--state-file` 不存在 = 第一次起；顺手建它，第二次起就走正常路径。"""
    if state_file is None:
        return True
    if state_file.exists():
        return False
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("started once\n", encoding="utf-8")
    return True


def _bind_twice(port: int) -> int:
    """同一进程里把端口 bind 两遍，第二遍的 OSError 原样打到 stderr——文案是平台真实的那句。"""
    a = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    b = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        a.bind(("127.0.0.1", port))
        a.listen(1)
        try:
            b.bind(("127.0.0.1", port))
        except OSError as exc:
            print(f"stub: OSError: {exc}", file=sys.stderr, flush=True)
            return 1
    finally:
        a.close()
        b.close()
    print("stub: 第二个 bind 居然成功了——桩没能模拟占用", file=sys.stderr, flush=True)
    return 4


def serve(args: argparse.Namespace) -> int:
    mode = args.fail_mode
    port = args.port
    if mode == "fallback":
        # 产品 resolve_port 顺延时的原话；然后真的在 Q 上服务——冒烟脚本对着 P 等是等不到的
        print(f"* 端口 {port} 被占用，改用 {port + 1}", flush=True)
        port = port + 1

    secret = "stub-" + secrets.token_urlsafe(16)
    if mode != "no-credentials":
        from tavotto.engine import session_client  # noqa: PLC0415  桩要用产品自己的路径公式

        session_client.publish_secret(port, secret)

    worker_code = "import time; time.sleep(600)"
    if args.worker_ignores_term and os.name != "nt":
        worker_code = (
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)"
        )
    worker = subprocess.Popen(
        [sys.executable, "-c", worker_code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if args.pid_file:
        Path(args.pid_file).write_text(
            json.dumps({"pid": os.getpid(), "worker": worker.pid}), encoding="utf-8"
        )
    ready_at = time.monotonic() + (args.delay if mode == "slow-ready" else 0.0)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802  http.server 的接口名
            if self.path == "/":
                if mode == "index-500":
                    self._send(500, b"boom", "text/plain")
                else:
                    self._send(200, b"<!doctype html><title>stub</title>", "text/html")
            elif self.path == "/api/version":
                if mode == "never-ready" or time.monotonic() < ready_at:
                    self._send(503, b'{"error":"not ready"}', "application/json")
                elif mode == "bad-json":
                    self._send(200, b"<html>not json</html>", "text/html")
                elif mode == "no-version-field":
                    self._send(200, b'{"build":"stub"}', "application/json")
                else:
                    self._send(200, b'{"version":"stub","build":"stub"}', "application/json")
            elif self.path == "/api/session/ping":
                got = self.headers.get("X-Tavotto-Auth")
                if mode != "reject-credentials" and got == secret:
                    self._send(200, b'{"ok":true}', "application/json")
                else:
                    self._send(401, b'{"error":"session_auth_required"}', "application/json")
            else:
                self._send(404, b"{}", "application/json")

        def log_message(self, fmt: str, *rest: object) -> None:
            print("stub: " + fmt % rest, file=sys.stderr, flush=True)

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        print(f"stub: OSError: {exc}", file=sys.stderr, flush=True)
        worker.kill()
        return 1
    print(f"stub: serving on 127.0.0.1:{port}（mode={mode}）", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    finally:
        worker.kill()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--fail-mode", choices=MODES, default="normal")
    ap.add_argument(
        "--state-file", type=Path, default=None, help="bind-busy / fallback 的「第一次」标记"
    )
    ap.add_argument("--pid-file", default=None, help="把自己与 worker 的 pid 写到这里")
    ap.add_argument("--delay", type=float, default=2.0, help="slow-ready：几秒之后才 200")
    ap.add_argument(
        "--worker-ignores-term", action="store_true", help="worker 对 SIGTERM 置 SIG_IGN（POSIX）"
    )
    args = ap.parse_args(argv)

    if args.fail_mode == "crash":
        print("stub: 启动即崩（--fail-mode crash）", file=sys.stderr, flush=True)
        return 3
    if args.fail_mode in ("bind-busy", "fallback") and not _first_start(args.state_file):
        args.fail_mode = "normal"
    if args.fail_mode == "bind-busy":
        return _bind_twice(args.port)
    return serve(args)


if __name__ == "__main__":
    sys.exit(main())
