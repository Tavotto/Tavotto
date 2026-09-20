"""U02 render_spike 的**最小应用 render child**：PDFium 只在一个子进程的主线程里跑，父进程串行调用。

PDFium 不是线程安全的（[W4]，pypdfium2 文档），所以 04 §4 定的形状是「有界的单个应用 render
child + 排队」。本模块给出那个形状的最小可运行版本，验证四条路径：

* **串行**：`RenderChildClient` 用一把锁把所有请求排成一队；child 单线程按序处理。多线程并发
  调用拿到的各是自己那份响应（`id` 对得上），child 处理序号严格递增。
* **超限**：像素预算（`width × height` 上限）在父子两侧都判——父进程先拒（不起 native 调用），
  child 再拒一次（父进程判据被绕过时仍挡得住）；child 侧还在 POSIX 上用 `RLIMIT_AS` 给地址
  空间设上限（**macOS 内核不强制 RLIMIT_AS**，实测只在 Linux 生效——写进 ADR，不假装）。
* **超时**：每个请求带 deadline；到点父进程 kill child、reap、标记死亡，本次返回
  `render_child_timeout`；**下一次请求自动重启 child**。
* **崩溃 / close**：child 被外力杀死或自己异常退出，读线程看到 EOF → 本次返回
  `render_child_died`，下一次请求重启；`close()` 发 `close` 请求等 child 自己退出，等不到就 kill。
  kill 之后一律 `wait()` reap（与 `engine/pool.py` 的 `_kill_and_reap()` 同一条纪律）。

协议：stdin / stdout 上的**行分隔 JSON**（一行一条）。请求 `{"id", "op", ...}`，响应
`{"id", "ok", ...}` 或 `{"id", "ok": false, "error": {"code", "message"}}`。op 闭集：
`ping` / `render` / `close`。child 端只依赖 pypdfium2（在 `child_main()` 里才 import），
客户端纯标准库——`tests/test_foundation_u02_render_child.py` 用一个假 child 验证客户端的
控制流，真 child 的用例在没有 pypdfium2 的机器上 skip 并写明理由。

冻结产物里的用法：同一个 exe 以 `--render-child` 再起自己（`freeze_spike.py`）。
"""

from __future__ import annotations

import json
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path

# Windows 上 stdout / stderr 被重定向成管道时会退回系统区域编码（cp1252 / cp936），
# 第一句中文就 UnicodeEncodeError；两条流都钉成 UTF-8（tests/test_windows_regressions.py 看护）。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

#: 默认像素预算：约 8000×8000 RGBA = 256 MiB。超过这个数的请求父进程直接拒绝。
DEFAULT_MAX_PIXELS = 64_000_000
#: child 地址空间上限（字节）；只在支持 RLIMIT_AS 的 POSIX 内核上生效。
DEFAULT_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024

ERROR_CODES = (
    "render_child_timeout",
    "render_child_died",
    "render_child_protocol",
    "pixel_budget_exceeded",
    "render_failed",
    "bad_request",
)


class RenderChildError(Exception):
    def __init__(self, code: str, message: str):
        assert code in ERROR_CODES, code
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


# ---------------------------------------------------------------------------
# 子进程侧
# ---------------------------------------------------------------------------
def _png_rgba(width: int, height: int, rgba: bytes) -> bytes:
    stride = width * 4
    raw = b"".join(b"\x00" + rgba[y * stride : (y + 1) * stride] for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def _apply_memory_limit(limit: int) -> str:
    try:
        import resource
    except ImportError:  # Windows
        return "unsupported"
    try:
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ValueError, OSError) as exc:
        return f"failed: {exc}"
    return "set" if sys.platform != "darwin" else "set-but-not-enforced-by-kernel"


def child_main(argv: list[str] | None = None) -> int:
    """child 主循环。pypdfium2 在这里才 import，且**只在这个线程**用。"""
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pixels", type=int, default=DEFAULT_MAX_PIXELS)
    ap.add_argument("--memory-limit", type=int, default=DEFAULT_MEMORY_LIMIT_BYTES)
    args = ap.parse_args(argv)
    mem = _apply_memory_limit(args.memory_limit)

    import pypdfium2 as pdfium

    out = sys.stdout.buffer
    seq = 0

    def reply(obj: dict) -> None:
        out.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        out.flush()

    for raw in sys.stdin.buffer:
        try:
            req = json.loads(raw.decode("utf-8"))
        except ValueError:
            reply(
                {"id": None, "ok": False, "error": {"code": "bad_request", "message": "not JSON"}}
            )
            continue
        rid, op = req.get("id"), req.get("op")
        seq += 1
        if op == "ping":
            reply({"id": rid, "ok": True, "seq": seq, "pid": os.getpid(), "memory_limit": mem})
        elif op == "close":
            reply({"id": rid, "ok": True, "seq": seq})
            return 0
        elif op == "render":
            try:
                width_px = int(req["width_px"])
                page_index = int(req.get("page", 0))
                pdf_path = Path(req["pdf"])
                out_path = Path(req["out"])
                doc = pdfium.PdfDocument(str(pdf_path))
                try:
                    page = doc[page_index]
                    w_pt, h_pt = page.get_size()
                    scale = width_px / w_pt
                    height_px = max(1, int(round(h_pt * scale)))
                    if width_px * height_px > int(req.get("max_pixels", args.max_pixels)):
                        raise RenderChildError(
                            "pixel_budget_exceeded",
                            f"{width_px}×{height_px} > {req.get('max_pixels', args.max_pixels)}",
                        )
                    t0 = time.perf_counter()
                    bitmap = page.render(
                        scale=scale,
                        rev_byteorder=True,
                        prefer_bgrx=False,
                        draw_annots=False,
                        fill_color=(255, 255, 255, 255),
                        force_bitmap_format=pdfium.raw.FPDFBitmap_BGRA,
                    )
                    png = _png_rgba(bitmap.width, bitmap.height, bytes(bitmap.buffer))
                    tmp = out_path.with_name(out_path.name + ".part")
                    tmp.write_bytes(png)
                    os.replace(tmp, out_path)
                    reply(
                        {
                            "id": rid,
                            "ok": True,
                            "seq": seq,
                            "width": bitmap.width,
                            "height": bitmap.height,
                            "ms": round((time.perf_counter() - t0) * 1000, 1),
                        }
                    )
                finally:
                    doc.close()
            except RenderChildError as exc:
                reply(
                    {
                        "id": rid,
                        "ok": False,
                        "seq": seq,
                        "error": {"code": exc.code, "message": exc.message},
                    }
                )
            except Exception as exc:  # child 把任何渲染失败都报成结构化错误，绝不带着半个响应退出
                reply(
                    {
                        "id": rid,
                        "ok": False,
                        "seq": seq,
                        "error": {
                            "code": "render_failed",
                            "message": f"{type(exc).__name__}: {exc}",
                        },
                    }
                )
        else:
            reply(
                {
                    "id": rid,
                    "ok": False,
                    "seq": seq,
                    "error": {"code": "bad_request", "message": f"unknown op {op!r}"},
                }
            )
    return 0


# ---------------------------------------------------------------------------
# 父进程侧
# ---------------------------------------------------------------------------
class RenderChildClient:
    """串行化的 child 客户端。`command` 是起 child 的 argv（默认：本解释器跑本模块）。"""

    def __init__(
        self,
        command: list[str] | None = None,
        *,
        max_pixels: int = DEFAULT_MAX_PIXELS,
        default_timeout: float = 30.0,
        env: dict[str, str] | None = None,
    ):
        self.command = command or [sys.executable, "-m", "dev.u02_spikes.render_child"]
        self.max_pixels = max_pixels
        self.default_timeout = default_timeout
        self.env = env
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._next_id = 0
        self.starts = 0
        #: 上一个 child 被 reap 时的退出码（None = 还没有 child 退出过）。kill 之后必须 wait()：
        #: 这个字段在 `_kill_and_reap()` 末尾由 returncode 赋值，没 reap 它就还是上一次的值。
        self.last_exit: int | None = None

    # -- 生命周期 ---------------------------------------------------------
    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc and self._proc.poll() is None else None

    @property
    def restarts(self) -> int:
        """第一次起 child 不算重启；之后每起一次（超时 kill 后 / 崩溃后 / 外力杀死后）算一次。"""
        return max(0, self.starts - 1)

    def _start(self) -> None:
        self.starts += 1
        self._lines = queue.Queue()
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=self.env,
        )
        proc = self._proc
        q = self._lines

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                q.put(line)
            q.put(None)  # EOF

        self._reader = threading.Thread(target=pump, name="render-child-reader", daemon=True)
        self._reader.start()

    def _kill_and_reap(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - kill 之后 wait 不该超时
            pass
        self.last_exit = proc.returncode
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
        self._proc = None

    def _ensure(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            if self._proc is not None:
                self._kill_and_reap()
            self._start()

    def close(self, timeout: float = 5.0) -> None:
        with self._lock:
            proc = self._proc
            if proc is None:
                return
            if proc.poll() is None:
                try:
                    self._send({"op": "close"})
                    proc.wait(timeout=timeout)
                except (OSError, subprocess.TimeoutExpired, RenderChildError):
                    pass
            self._kill_and_reap()

    # -- 请求 -------------------------------------------------------------
    def _send(self, req: dict) -> int:
        assert self._proc is not None and self._proc.stdin is not None
        self._next_id += 1
        req["id"] = self._next_id
        try:
            self._proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
            self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise RenderChildError("render_child_died", f"write failed: {exc}") from exc
        return req["id"]

    def _recv(self, rid: int, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RenderChildError("render_child_timeout", f"no response within {timeout}s")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                raise RenderChildError(
                    "render_child_timeout", f"no response within {timeout}s"
                ) from None
            if line is None:
                raise RenderChildError("render_child_died", "child closed its stdout")
            try:
                resp = json.loads(line.decode("utf-8"))
            except ValueError as exc:
                raise RenderChildError("render_child_protocol", f"not JSON: {line[:80]!r}") from exc
            if resp.get("id") != rid:
                # 上一次超时后残留的响应：丢掉（child 已被 kill，理论上到不了这里；防御性）
                continue
            return resp

    def request(self, op: str, *, timeout: float | None = None, **fields) -> dict:
        timeout = self.default_timeout if timeout is None else timeout
        with self._lock:
            self._ensure()
            try:
                rid = self._send({"op": op, **fields})
                resp = self._recv(rid, timeout)
            except RenderChildError as exc:
                if exc.code in (
                    "render_child_timeout",
                    "render_child_died",
                    "render_child_protocol",
                ):
                    self._kill_and_reap()
                raise
            if not resp.get("ok"):
                err = resp.get("error") or {}
                raise RenderChildError(err.get("code", "render_failed"), err.get("message", ""))
            return resp

    def ping(self, timeout: float | None = None) -> dict:
        return self.request("ping", timeout=timeout)

    def render(
        self,
        pdf: Path,
        out: Path,
        width_px: int,
        *,
        page: int = 0,
        page_size_pt: tuple[float, float] | None = None,
        timeout: float | None = None,
    ) -> dict:
        """父进程先按像素预算拒绝（不起 native 调用）；通过才交给 child。

        `page_size_pt` 已知时精确判（宽 × 按比例算出的高）；未知时只能按宽度的方形上界粗判，
        精确的那一次由 child 在打开页面之后再判——两侧都判，父侧判不了的 child 兜底。"""
        if width_px <= 0:
            raise RenderChildError("bad_request", "width_px must be positive")
        if page_size_pt:
            w_pt, h_pt = page_size_pt
            height_px = max(1, int(round(h_pt * width_px / w_pt)))
            if width_px * height_px > self.max_pixels:
                raise RenderChildError(
                    "pixel_budget_exceeded", f"{width_px}×{height_px} > {self.max_pixels}"
                )
        elif width_px * width_px > self.max_pixels * 4:
            raise RenderChildError(
                "pixel_budget_exceeded", f"width {width_px}px cannot fit {self.max_pixels} pixels"
            )
        return self.request(
            "render",
            timeout=timeout,
            pdf=str(pdf),
            out=str(out),
            width_px=int(width_px),
            page=int(page),
            max_pixels=self.max_pixels,
        )


if __name__ == "__main__":
    sys.exit(child_main())
