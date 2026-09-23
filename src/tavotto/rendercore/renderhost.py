"""RenderHost —— 父进程里 render child 的**唯一**客户端（统一实施包 U07，ADR 0066）。

04_ARCHITECTURE §4：*PDFium 生命周期集中，进程内部串行；首轮采用有界单个应用 render child + 排队*。这个
类就是那句话：一把锁把 probe / render / inspect 排成一队（RC-047：并发 preview / probe / export 之间没有
进程内 native 并发——native 根本不在本进程里），一个有界的等待队列给背压（RC-062：`max_waiting` 个
请求在等锁时第 N+1 个立刻 `render_queue_full`，不无限堆积），每个请求带 deadline（到点 kill → reap →
本次 `render_child_timeout` → 下一次自动重启），child 崩溃 / 被外力杀死 → `render_child_died` → 下一次重启。

纯标准库；候选包只在 child 进程里（`renderchild.child_main`）。`stdout=PIPE` 由专门的读线程在 child 运行
期间持续排空（与 `engine/pool.py` 读 worker 同一形状）；`stderr` 落到 DEVNULL——child 的诊断走结构化错误。

像素经文件不经管道：`render()` 在 `scratch_dir` 里给 child 一个目标文件，读回后立刻删，返回
`raster.RasterBuffer`（父进程自己的 `bytes`，与 child 的生命周期无关）。

进程级共享实例：`shared()`（懒建、一个进程一个），`shutdown_shared()`（测试与关停用）。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from .raster import RasterBuffer, RasterError
from .renderchild import (
    BAND_OVERLAP,
    DEFAULT_MAX_PIXELS,
    DEFAULT_MAX_TOTAL_PIXELS,
    ERROR_CODES,
    RenderChildError,
    band_rows_for,
    child_argv,
)

__all__ = [
    "DEFAULT_MAX_PIXELS",
    "DEFAULT_MAX_TOTAL_PIXELS",
    "ERROR_CODES",
    "RenderChildError",
    "RenderHost",
    "band_rows_for",
    "shared",
    "shutdown_shared",
]


class RenderHost:
    """串行化的 child 客户端。`command` 是起 child 的 argv（默认 `renderchild.child_argv()`）。"""

    def __init__(
        self,
        command: list[str] | None = None,
        *,
        max_pixels: int = DEFAULT_MAX_PIXELS,
        max_total_pixels: int = DEFAULT_MAX_TOTAL_PIXELS,
        default_timeout: float = 60.0,
        max_waiting: int = 32,
        env: dict[str, str] | None = None,
        scratch_dir: Path | None = None,
    ) -> None:
        self.command = list(command) if command else child_argv()
        self.max_pixels = int(max_pixels)
        #: 条带栅格（ADR 0077 P2）的整页上限；每一带仍受 `max_pixels` 约束
        self.max_total_pixels = int(max_total_pixels)
        self.default_timeout = float(default_timeout)
        self.max_waiting = int(max_waiting)
        self.env = env
        self.scratch_dir = Path(scratch_dir) if scratch_dir else None
        self._lock = threading.Lock()
        #: 有界队列：在等 `_lock` 的请求数（含正在执行的那一个）不许超过 max_waiting + 1
        self._slots = threading.BoundedSemaphore(self.max_waiting + 1)
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._next_id = 0
        self.starts = 0
        #: 上一个 child 被 reap 时的退出码（None = 还没有 child 退出过）。kill 之后必须 wait()：
        #: 这个字段在 `_kill_and_reap()` 末尾由 returncode 赋值，没 reap 它就还是上一次的值。
        self.last_exit: int | None = None
        self.requests = 0

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
        try:
            self._proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self.env,
            )
        except OSError as exc:
            # exe 不在 / 没权限 / 冻结产物的命令不对：结构化错误，让 job 把它落到该格式的 format_failed，
            # 而不是整个作业炸掉（Codex #471 第三轮 P2）
            self._proc = None
            raise RenderChildError(
                "render_child_spawn_failed", f"起不来 render child {self.command[:1]}: {exc}"
            ) from exc
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
        """发 `close` 等 child 自己退出；等不到就 kill。之后 `_kill_and_reap()`：进程一定被 wait 回收。"""
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
            if not isinstance(resp, dict) or resp.get("id") != rid:
                # child 的日志行 / 上一次超时后残留的响应：丢掉（超时后 child 已被 kill，理论上到不了这里）
                continue
            return resp

    def request(
        self,
        op: str,
        *,
        timeout: float | None = None,
        verify: Callable[[dict], None] | None = None,
        **fields,
    ) -> dict:
        """一个 deadline 管到底：等锁 + 起 child + 发 + 收都算在 `timeout` 里。等锁超时**不 kill child**——
        它正在做别人的请求，只是本次来不及（Codex #471 P2：否则短超时的预览要排在前面每个慢请求后面才开始计时）。
        `verify(resp)` 在**锁内**、响应到手之后跑：它抛的 `render_child_protocol` 与收不到 JSON 同一处置
        （kill + reap），排在后面的请求绝不会拿到一个刚说了谎的 child（第三轮 P2）。"""
        timeout = self.default_timeout if timeout is None else float(timeout)
        deadline = time.monotonic() + timeout
        if not self._slots.acquire(blocking=False):
            raise RenderChildError(
                "render_queue_full", f"已有 {self.max_waiting} 个请求在排队（有界队列，背压）"
            )
        try:
            if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
                raise RenderChildError(
                    "render_child_timeout",
                    f"等待 render child 空闲超过 {timeout}s（child 未被打断）",
                )
            try:
                self.requests += 1
                self._ensure()
                try:
                    rid = self._send({"op": op, **fields})
                    resp = self._recv(rid, max(0.0, deadline - time.monotonic()))
                    if verify is not None and resp.get("ok"):
                        verify(resp)
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
                    code = err.get("code", "render_failed")
                    if code not in ERROR_CODES:
                        code = "render_failed"
                    raise RenderChildError(code, str(err.get("message", "")))
                return resp
            finally:
                self._lock.release()
        finally:
            self._slots.release()

    def ping(self, timeout: float | None = None) -> dict:
        return self.request("ping", timeout=timeout)

    def probe(self, pdf: Path, *, page: int = 0, timeout: float | None = None) -> dict:
        """`{pages, width_pt, height_pt (含 /UserUnit、已按 /Rotate 转), rotation, user_unit, media_box, crop_box}`。"""
        return self.request("probe", timeout=timeout, pdf=str(pdf), page=int(page))

    def size(self, pdf: Path, *, page: int = 0, timeout: float | None = None) -> dict:
        """`{pages, width_pt, height_pt}`——与 `probe` 同一个可见尺寸，但不加载页（不解析内容流）。"""
        return self.request("size", timeout=timeout, pdf=str(pdf), page=int(page))

    def inspect(self, pdf: Path, *, page: int = 0, timeout: float | None = None) -> dict:
        return self.request("inspect", timeout=timeout, pdf=str(pdf), page=int(page))

    def _budget(self, width_px: int, height_px: int) -> None:
        if width_px * height_px > self.max_pixels:
            raise RenderChildError(
                "pixel_budget_exceeded", f"{width_px}×{height_px} > {self.max_pixels}"
            )

    def render(
        self,
        pdf: Path,
        *,
        width_px: int | None = None,
        dpi: float | None = None,
        page: int = 0,
        transparent: bool = False,
        page_size_pt: tuple[float, float] | None = None,
        timeout: float | None = None,
        band: tuple[int, int] | None = None,
    ) -> RasterBuffer:
        """页 → `RasterBuffer`（RGBA，straight alpha，父进程自己的字节）。

        `width_px`（按宽定比例，画布预览）与 `dpi`（导出）二选一。父进程先按像素预算拒绝（不起 native
        调用）：`page_size_pt` 已知时精确判；未知时只能粗判（按宽的方形上界），精确的那一次由 child 在
        打开页面之后再判——两侧都判，父侧判不了的 child 兜底。
        """
        if (width_px is None) == (dpi is None):
            raise RenderChildError("bad_request", "width_px 与 dpi 二选一")
        if width_px is not None:
            width_px = int(width_px)
            if width_px <= 0:
                raise RenderChildError("bad_request", "width_px must be positive")
            if page_size_pt:
                w_pt, h_pt = page_size_pt
                self._budget(width_px, max(1, int(round(h_pt * width_px / w_pt))))
            elif width_px * width_px > self.max_pixels * 4:
                raise RenderChildError(
                    "pixel_budget_exceeded",
                    f"width {width_px}px cannot fit {self.max_pixels} pixels",
                )
        else:
            dpi = float(dpi)
            if not dpi > 0:
                raise RenderChildError("bad_request", "dpi must be positive")
            if page_size_pt:
                w_pt, h_pt = page_size_pt
                full = (
                    max(1, int(round(w_pt * dpi / 72.0))),
                    max(1, int(round(h_pt * dpi / 72.0))),
                )
                if band is None:
                    self._budget(*full)
                else:
                    # 条带：这一带受 max_pixels，整页受 max_total_pixels（父侧先判，child 打开页面后再判一次）
                    if full[0] * full[1] > self.max_total_pixels:
                        raise RenderChildError(
                            "pixel_budget_exceeded",
                            f"整页 {full[0]}×{full[1]} > {self.max_total_pixels}",
                        )
                    self._budget(full[0], int(band[1]) + 2 * BAND_OVERLAP)  # 含上下重叠
        if band is not None and width_px is not None:
            raise RenderChildError("bad_request", "条带只给按 dpi 的导出，不给按宽的预览")
        # 条带：父进程独立算出的整页尺寸（有 page_size_pt 才算得出），用来核 child 回报的整页
        band_full = full if band is not None and dpi is not None and page_size_pt else None
        fd, name = tempfile.mkstemp(prefix="render-", suffix=".rgba", dir=self.scratch_dir)
        os.close(fd)
        out = Path(name)
        got: dict[str, RasterBuffer] = {}

        def verify(resp: dict) -> None:
            # 锁内读像素文件、核长度、**把 RasterBuffer 整个建出来**：child 说的 width / height / channels / stride
            # 与字节对不上（bytes 对得上也可能对不上）同样是协议失败 → 当场 kill + reap，释放锁之后没人会再拿到
            # 这个 child；建不出来的 RasterError / 类型错误翻译成 render_child_protocol，job 落到该格式的
            # format_failed 而不是整个作业 export_failed（Codex #471 第五轮 P2）
            try:
                samples = out.read_bytes()
            except OSError as exc:
                # child 说成功、像素文件却不在 / 读不了：同样是协议不可信（Codex #471 第七轮 P2）——裸 OSError
                # 会越过 RenderChildError 的处置，锁释放而 child 不 reap、job 整个炸
                raise RenderChildError(
                    "render_child_protocol", f"child 说成功，像素文件读不了: {exc}"
                ) from exc
            try:
                # None / 非数字也是协议不可信（Codex #471 第八轮 P2）
                declared = int(resp.get("bytes"))
            except (TypeError, ValueError) as exc:
                raise RenderChildError(
                    "render_child_protocol", f"响应的 bytes 字段不是数: {resp.get('bytes')!r}"
                ) from exc
            if len(samples) != declared:
                raise RenderChildError(
                    "render_child_protocol",
                    f"像素文件 {len(samples)} 字节与响应说的 {declared} 不符",
                )
            if band is not None and (resp.get("band_y0"), resp.get("height")) != (
                int(band[0]),
                int(band[1]),
            ):
                raise RenderChildError(
                    "render_child_protocol",
                    f"要的行带 {tuple(band)}，child 给的是 ({resp.get('band_y0')}, {resp.get('height')})",
                )
            if (
                band is not None
                and band_full is not None
                and (
                    resp.get("width"),
                    resp.get("full_height"),
                )
                != band_full
            ):
                # 整页尺寸由父进程按页面尺寸 × dpi 独立算出；child 算出的整页若不同（页盒 / UserUnit 的处理出了偏差），
                # 按计划高度拼出来的是一张被悄悄裁掉或错位的图——协议失败（Codex #513 P2）
                raise RenderChildError(
                    "render_child_protocol",
                    f"child 的整页是 {resp.get('width')}×{resp.get('full_height')}，父进程算的是 "
                    f"{band_full[0]}×{band_full[1]}",
                )
            try:
                got["buf"] = RasterBuffer(
                    width=int(resp["width"]),
                    height=int(resp["height"]),
                    channels=int(resp["channels"]),
                    samples=samples,
                    stride=int(resp["stride"]),
                    dpi=float(dpi) if dpi is not None else None,
                )
            except (RasterError, KeyError, TypeError, ValueError) as exc:
                raise RenderChildError(
                    "render_child_protocol", f"响应说的尺寸与像素字节不成一张图: {exc}"
                ) from exc

        try:
            self.request(
                "render",
                timeout=timeout,
                verify=verify,
                pdf=str(pdf),
                out=str(out),
                page=int(page),
                width_px=width_px,
                dpi=dpi,
                transparent=bool(transparent),
                max_pixels=self.max_pixels,
                **(
                    {
                        "band_y0": int(band[0]),
                        "band_rows": int(band[1]),
                        "band_overlap": BAND_OVERLAP,
                        "max_total_pixels": self.max_total_pixels,
                    }
                    if band is not None
                    else {}
                ),
            )
            buf = got["buf"]
        finally:
            # 目标文件与 child 的 `.part` 一起清：超时 / 崩溃可能停在 write_bytes 之后、os.replace 之前，
            # 每次 mkstemp 名字都不同，不清就攒成一堆接近像素预算的孤儿（Codex #471 P2）
            for stale in (out, out.with_name(out.name + ".part")):
                try:
                    stale.unlink()
                except OSError:
                    pass
        return buf


# ---------------------------------------------------------------------------
# 进程级共享实例（首轮：一个应用一个 child）
# ---------------------------------------------------------------------------
_SHARED: RenderHost | None = None
_SHARED_LOCK = threading.Lock()


def shared() -> RenderHost:
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = RenderHost()
        return _SHARED


def shutdown_shared() -> None:
    global _SHARED
    with _SHARED_LOCK:
        host, _SHARED = _SHARED, None
    if host is not None:
        host.close()


def _self_test() -> int:  # pragma: no cover - 手工诊断用
    host = RenderHost()
    try:
        print(json.dumps(host.ping(), ensure_ascii=False))
    finally:
        host.close()
    return 0


if __name__ == "__main__":
    sys.exit(_self_test())
