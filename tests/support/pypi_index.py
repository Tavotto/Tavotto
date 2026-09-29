"""本地的最小 PyPI 简单索引（PEP 503）：给「官方源慢 / 坏 → 换镜像」的用例一个**真 pip** 能说话的对手。

* `make_wheel()`：造一个纯 Python 的 wheel（带一段随机字节、不压缩——限速要有「下了一段、没下完」的中间态）；
* `SimpleIndex`：`127.0.0.1` 上的 HTTP 服务。`/simple/<名>/` 回链接页（链接带 `#sha256=`，pip 会核），
  `/files/<wheel>` 回文件。`mode`：`ok` / `fail`（一律 503）/ `hang`（连上了不回）/ `corrupt`（文件翻一个字节、链接上的 hash 不变——
  一个回错字节的镜像）；`throttle_bps` 限速（16 KiB 一段）。每个请求记进 `requests`。

不联公网：用例把 `build_argv` 指到这里（官方源一个实例、镜像一个实例），产品代码决定**何时**换到镜像那一个。
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import io
import os
import threading
import time
import zipfile
from pathlib import Path

NAME = "tavtest"
VERSION = "1.0"
CHUNK = 16 * 1024


def make_wheel(dest_dir: Path, *, padding: int = 400 * 1024) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    wheel = dest_dir / f"{NAME}-{VERSION}-py3-none-any.whl"
    info = f"{NAME}-{VERSION}.dist-info"
    files = {
        f"{NAME}/__init__.py": b"VALUE = 1\n",
        f"{NAME}/padding.bin": os.urandom(padding),
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {NAME}\nVersion: {VERSION}\n".encode(),
        f"{info}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: tavotto-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record = []
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append(f"{name},sha256={digest},{len(data)}")
    record.append(f"{info}/RECORD,,")
    files[f"{info}/RECORD"] = ("\n".join(record) + "\n").encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    wheel.write_bytes(buf.getvalue())
    return wheel


class SimpleIndex:
    def __init__(self, wheel: Path):
        self.wheel = Path(wheel)
        self.data = self.wheel.read_bytes()
        self.sha256 = hashlib.sha256(self.data).hexdigest()
        self.mode = "ok"
        self.throttle_bps = 0
        self.requests: list[str] = []
        self.release = threading.Event()  # `mode == "hang"` 时放行
        self._server = None
        self._thread = None

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}/simple"

    def __enter__(self):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):  # 静音
                pass

            def _head(self, status: int, length: int, ctype: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(length))
                self.end_headers()

            def do_GET(self):  # noqa: N802
                outer.requests.append(self.path)
                if outer.mode == "hang":
                    # 连上了、迟迟不回（索引页慢到极点的形状）；pip 的套接字超时是 15 s，用例的判据远早于它
                    outer.release.wait(60)
                    self._head(503, 0, "text/plain")
                    return
                if outer.mode == "fail":
                    self._head(503, 0, "text/plain")
                    return
                if self.path.rstrip("/") == f"/simple/{NAME}":
                    link = f"/files/{outer.wheel.name}#sha256={outer.sha256}"
                    body = f'<html><body><a href="{link}">{outer.wheel.name}</a></body></html>'
                    data = body.encode()
                    self._head(200, len(data), "text/html")
                    self.wfile.write(data)
                    return
                if self.path != f"/files/{outer.wheel.name}":
                    self._head(404, 0, "text/plain")
                    return
                data = outer.data
                if outer.mode == "corrupt":
                    mid = len(data) // 2
                    data = data[:mid] + bytes([data[mid] ^ 0xFF]) + data[mid + 1 :]
                self._head(200, len(data), "application/octet-stream")
                for i in range(0, len(data), CHUNK):
                    try:
                        self.wfile.write(data[i : i + CHUNK])
                        self.wfile.flush()
                    except OSError:
                        return  # 客户端（被杀掉的 pip）先走了
                    if outer.throttle_bps:
                        time.sleep(CHUNK / outer.throttle_bps)

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        assert self._server is not None
        self.release.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=10)
